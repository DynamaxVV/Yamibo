"""Explicit borrowed connection: the caller owns commit, rollback and close."""


class BorrowedConnection:
    def __init__(self, connection):
        self.connection = connection

    def commit(self):
        pass

    def close(self):
        pass

    def rollback(self):
        # A repository must not retry part of an enclosing transaction.
        raise RuntimeError("Enclosing transaction must be retried as a whole")

    def __getattr__(self, name):
        return getattr(self.connection, name)
