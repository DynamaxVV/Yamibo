from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.engine import Connection, Engine, Result, make_url

from yamibo_mcp.config import Settings, load_settings
from yamibo_mcp.db.alembic_runner import upgrade_postgres_schema
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.observability import install_engine_observability, note_pool_timeout


class RowProxy:
    __slots__ = ("_row",)

    def __init__(self, row: Any) -> None:
        self._row = row

    def __getitem__(self, key: int | slice | str) -> Any:
        if isinstance(key, str):
            return self._row._mapping[key]
        return self._row[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._row._mapping.get(key, default)

    def keys(self):
        return self._row._mapping.keys()

    def items(self):
        return self._row._mapping.items()

    def values(self):
        return self._row._mapping.values()

    def __contains__(self, key: object) -> bool:
        return key in self._row._mapping

    def __iter__(self):
        return iter(self._row)

    def __len__(self) -> int:
        return len(self._row)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._row._mapping[name]
        except KeyError as exc:  # pragma: no cover - standard attribute fallback
            raise AttributeError(name) from exc

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return repr(self._row)


class ResultProxy:
    __slots__ = ("_result",)

    def __init__(self, result: Result[Any]) -> None:
        self._result = result

    def _wrap_row(self, row: Any | None) -> RowProxy | None:
        return None if row is None else RowProxy(row)

    def fetchone(self) -> RowProxy | None:
        return self._wrap_row(self._result.fetchone())

    def fetchmany(self, size: int | None = None) -> list[RowProxy]:
        rows = self._result.fetchmany(size) if size is not None else self._result.fetchmany()
        return [RowProxy(row) for row in rows]

    def fetchall(self) -> list[RowProxy]:
        return [RowProxy(row) for row in self._result.fetchall()]

    def first(self) -> RowProxy | None:
        return self._wrap_row(self._result.first())

    def one(self) -> RowProxy:
        return RowProxy(self._result.one())

    def one_or_none(self) -> RowProxy | None:
        return self._wrap_row(self._result.one_or_none())

    def scalar(self):
        return self._result.scalar()

    def scalar_one(self):
        return self._result.scalar_one()

    def scalar_one_or_none(self):
        return self._result.scalar_one_or_none()

    def keys(self):
        return self._result.keys()

    def __iter__(self):
        for row in self._result:
            yield RowProxy(row)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._result, name)


class DatabaseConnection:
    __slots__ = ("_conn", "backend", "row_factory")

    def __init__(self, conn: Connection, *, backend: str) -> None:
        self._conn = conn
        self.backend = backend
        self.row_factory = None

    def execute(self, statement, parameters=None) -> ResultProxy:
        parameters = _sanitize_bound_parameters(parameters)
        if isinstance(statement, str):
            statement, parameters = _normalize_statement(statement, parameters)
            result = self._conn.execute(text(statement), parameters)
        else:
            result = self._conn.execute(statement, parameters)
        return ResultProxy(result)

    def executemany(self, statement, seq_of_parameters: Iterable[object]) -> ResultProxy:
        payload = [_sanitize_bound_parameters(parameters) for parameters in seq_of_parameters]
        if not payload:
            return self.execute(statement)
        if isinstance(statement, str):
            statement, payload = _normalize_executemany(statement, payload)
            result = self._conn.execute(text(statement), payload)
        else:
            result = self._conn.execute(statement, payload)
        return ResultProxy(result)

    def executescript(self, script: str) -> None:
        for statement in _split_sql_script(script):
            self.execute(statement)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def begin(self):
        return self._conn.begin()

    def enable_load_extension(self, enabled: bool) -> None:
        driver_connection = getattr(self._conn.connection, "driver_connection", None)
        if driver_connection is None:
            driver_connection = self._conn.connection
        method = getattr(driver_connection, "enable_load_extension", None)
        if method is None:
            raise AttributeError("enable_load_extension")
        method(enabled)

    def load_extension(self, path: str) -> None:
        driver_connection = getattr(self._conn.connection, "driver_connection", None)
        if driver_connection is None:
            driver_connection = self._conn.connection
        method = getattr(driver_connection, "load_extension", None)
        if method is None:
            raise AttributeError("load_extension")
        method(path)

    def in_transaction(self) -> bool:
        return self._conn.in_transaction()

    @property
    def raw_connection(self) -> Connection:
        return self._conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()
        return False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def row_to_dict(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row._mapping)


def connect(
    db_path: Path | str | Settings | None = None,
    *,
    pool_role: str = "web",
    bootstrap: bool = True,
) -> DatabaseConnection:
    """打开一个数据库连接。

    参数:
        db_path: 数据库路径、Settings 对象或 None（使用默认配置）。
        pool_role: 连接池角色 —— ``"web"`` 用于短连接（Web 请求），
                   ``"daemon"`` 用于长连接（后台 job 处理）。
                   仅 PostgreSQL 生效；SQLite 忽略此参数。
        bootstrap: 是否在打开连接前确保 schema 已初始化；Web 应用在启动阶段完成后，
                   请求连接应传 ``False``。
    """
    settings = db_path if isinstance(db_path, Settings) else load_settings()
    # A Path that matches settings.db_path is NOT an explicit SQLite choice —
    # it's the caller passing along the default path. Respect db_backend.
    explicit_sqlite_path = (
        db_path is not None
        and not isinstance(db_path, Settings)
        and str(db_path) != str(settings.db_path)
    )
    if not explicit_sqlite_path and settings.db_backend == "postgres":
        if not settings.db_url:
            raise ValueError("db_url is required when db_backend=postgres")
        pool_min = settings.db_pool_min
        pool_max = settings.db_pool_max
        if pool_role == "daemon":
            pool_min, pool_max = _daemon_pool_size(settings)
        engine = _postgres_engine_for_role(
            settings.db_url,
            pool_min,
            pool_max,
            settings.db_pool_timeout,
            settings.db_connect_timeout,
            settings.db_ssl_mode,
            str(settings.db_ssl_root_cert) if settings.db_ssl_root_cert else None,
            pool_role=pool_role,
        )
        if bootstrap:
            _bootstrap_postgres_database(engine, settings.db_schema)
        try:
            return DatabaseConnection(engine.connect(), backend="postgres")
        except SQLAlchemyTimeoutError:
            note_pool_timeout(engine)
            raise

    db_path_value = _resolve_db_path(db_path, settings)
    if bootstrap:
        _bootstrap_sqlite_database(db_path_value, settings.db_connect_timeout)
    engine = _sqlite_engine(db_path_value, settings.db_connect_timeout)
    return DatabaseConnection(engine.connect(), backend="sqlite")


@contextmanager
def transaction(conn: DatabaseConnection):
    if conn.backend == "postgres":
        with conn.begin():
            yield conn
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _resolve_db_path(db_path: Path | str | Settings | None, settings: Settings) -> Path:
    if isinstance(db_path, Settings):
        return db_path.db_path
    if db_path is None:
        return settings.db_path
    return Path(db_path).expanduser()


def _daemon_pool_size(settings: Settings) -> tuple[int, int]:
    """返回 daemon 连接池的 (pool_min, pool_max)。

    每个 worker 最多同时持有 2 个连接（run_once + _write_series_artifacts），
    乘以可能的 2 个 daemon 进程 + 余量 = parallelism * 4 + 2。
    """
    parallelism = max(getattr(settings, "worker_parallelism", 1), 1)
    pool_min = max(parallelism, 2)
    pool_max = max(parallelism * 4 + 2, pool_min + 3)
    return pool_min, pool_max


def _normalize_postgres_url(db_url: str) -> str:
    url = make_url(db_url)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


@lru_cache(maxsize=None)
def _postgres_engine_for_role(
    db_url: str,
    pool_min: int,
    pool_max: int,
    pool_timeout: float,
    connect_timeout: float,
    ssl_mode: str,
    ssl_root_cert: str | None,
    *,
    pool_role: str = "web",
) -> Engine:
    """返回 PostgreSQL 引擎，按角色隔离连接池。

    ``pool_role="daemon"`` 使用独立的连接池，池大小由 ``_daemon_pool_size()``
    根据 ``worker_parallelism`` 动态计算，避免 daemon 长时间持有连接时阻塞 Web 请求。
    """
    connect_args: dict[str, object] = {"connect_timeout": connect_timeout}
    if ssl_mode:
        connect_args["sslmode"] = ssl_mode
    if ssl_root_cert is not None:
        connect_args["sslrootcert"] = ssl_root_cert
    engine = create_engine(
        _normalize_postgres_url(db_url),
        pool_size=pool_min,
        max_overflow=max(pool_max - pool_min, 0),
        pool_timeout=pool_timeout,
        pool_recycle=600,
        connect_args=connect_args,
    )
    install_engine_observability(engine)
    return engine


@lru_cache(maxsize=None)
def _sqlite_engine(db_path: Path, connect_timeout: float) -> Engine:
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path.resolve()}",
        connect_args={"timeout": connect_timeout, "check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA busy_timeout = 30000")
        finally:
            cursor.close()

    install_engine_observability(engine)
    return engine


def _bootstrap_sqlite_database(db_path: Path, connect_timeout: float) -> None:
    if _is_bootstrapped(db_path):
        return
    engine = _sqlite_engine(db_path, connect_timeout)
    with engine.connect() as raw_conn:
        migrate(DatabaseConnection(raw_conn, backend="sqlite"))
    _mark_bootstrapped(db_path)


def _bootstrap_postgres_database(engine: Engine, schema: str) -> None:
    cache_key = _postgres_bootstrap_key(engine, schema)
    if cache_key in _BOOTSTRAPPED_POSTGRES_DATABASES:
        return
    with engine.connect() as raw_conn:
        upgrade_postgres_schema(raw_conn, schema=schema)
    _BOOTSTRAPPED_POSTGRES_DATABASES.add(cache_key)


_BOOTSTRAPPED_SQLITE_DATABASES: set[str] = set()
_BOOTSTRAPPED_POSTGRES_DATABASES: set[str] = set()


def _is_bootstrapped(db_path: Path) -> bool:
    return str(db_path.resolve()) in _BOOTSTRAPPED_SQLITE_DATABASES


def _mark_bootstrapped(db_path: Path) -> None:
    _BOOTSTRAPPED_SQLITE_DATABASES.add(str(db_path.resolve()))


def _postgres_bootstrap_key(engine: Engine, schema: str) -> str:
    return f"{engine.url.render_as_string(hide_password=True)}::{schema}"


def _normalize_statement(statement: str, parameters):
    if parameters is None:
        return statement, None
    if isinstance(parameters, Mapping):
        return statement, parameters
    if isinstance(parameters, (str, bytes, bytearray)):
        return statement, parameters
    if _contains_qmark(statement):
        statement, names = _rewrite_qmark_placeholders(statement)
        values = list(parameters)
        if len(values) != len(names):
            raise ValueError("parameter count does not match SQL placeholders")
        return statement, {name: value for name, value in zip(names, values, strict=True)}
    return statement, parameters


def _normalize_executemany(statement: str, payload: list[object]) -> tuple[str, list[dict[str, object]] | list[object]]:
    if not payload:
        return statement, payload
    first = payload[0]
    if isinstance(first, Mapping):
        return statement, payload  # type: ignore[return-value]
    if isinstance(first, (str, bytes, bytearray)):
        return statement, payload
    if not _contains_qmark(statement):
        return statement, payload
    rewritten, names = _rewrite_qmark_placeholders(statement)
    rewritten_payload = []
    for parameters in payload:
        values = list(parameters)  # type: ignore[arg-type]
        if len(values) != len(names):
            raise ValueError("parameter count does not match SQL placeholders")
        rewritten_payload.append({name: value for name, value in zip(names, values, strict=True)})
    return rewritten, rewritten_payload


def _sanitize_bound_parameters(parameters):
    if parameters is None:
        return None
    if isinstance(parameters, Mapping):
        return {key: _sanitize_bound_value(value) for key, value in parameters.items()}
    if isinstance(parameters, tuple):
        return tuple(_sanitize_bound_value(value) for value in parameters)
    if isinstance(parameters, list):
        return [_sanitize_bound_value(value) for value in parameters]
    return parameters


def _sanitize_bound_value(value):
    # ponytail: strip NUL bytes at the DB boundary; PostgreSQL rejects them and callers pass plain text.
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, Mapping):
        return {key: _sanitize_bound_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_sanitize_bound_value(item) for item in value)
    if isinstance(value, list):
        return [_sanitize_bound_value(item) for item in value]
    return value


def _contains_qmark(statement: str) -> bool:
    in_single = False
    in_double = False
    for index, char in enumerate(statement):
        if char == "'" and not in_double:
            if in_single and index + 1 < len(statement) and statement[index + 1] == "'":
                continue
            in_single = not in_single
        elif char == '"' and not in_single:
            if in_double and index + 1 < len(statement) and statement[index + 1] == '"':
                continue
            in_double = not in_double
        elif char == "?" and not in_single and not in_double:
            return True
    return False


def _rewrite_qmark_placeholders(statement: str) -> tuple[str, list[str]]:
    parts: list[str] = []
    names: list[str] = []
    in_single = False
    in_double = False
    placeholder_index = 0
    i = 0
    while i < len(statement):
        char = statement[i]
        if char == "'" and not in_double:
            if in_single and i + 1 < len(statement) and statement[i + 1] == "'":
                parts.append("''")
                i += 2
                continue
            in_single = not in_single
            parts.append(char)
        elif char == '"' and not in_single:
            if in_double and i + 1 < len(statement) and statement[i + 1] == '"':
                parts.append('""')
                i += 2
                continue
            in_double = not in_double
            parts.append(char)
        elif char == "?" and not in_single and not in_double:
            name = f"p{placeholder_index}"
            placeholder_index += 1
            names.append(name)
            parts.append(f":{name}")
        else:
            parts.append(char)
        i += 1
    return "".join(parts), names


def _split_sql_script(script: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(script):
        char = script[i]
        if char == "'" and not in_double:
            if in_single and i + 1 < len(script) and script[i + 1] == "'":
                current.append("''")
                i += 2
                continue
            in_single = not in_single
            current.append(char)
        elif char == '"' and not in_single:
            if in_double and i + 1 < len(script) and script[i + 1] == '"':
                current.append('""')
                i += 2
                continue
            in_double = not in_double
            current.append(char)
        elif char == ";" and not in_single and not in_double:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(char)
        i += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements
