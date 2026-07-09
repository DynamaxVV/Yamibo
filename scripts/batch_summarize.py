"""
Batch summarize YAML frontmatter ai_summary field for forum thread markdown files.
Batches multiple files per LLM call for speed.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "obsidian" / "05-anime"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "yamibo.local.json"
CHECKPOINT_PATH = Path(__file__).resolve().parent / ".summarize_checkpoint.txt"

LLM_BASE_URL = "https://opencode.ai/zen/go/v1/chat/completions"
LLM_API_KEY = "sk-J0OChZKifWpvB9Fc49Bsb1idfW2ssQN9VYDEj6DQXVTEiDSG4mCtcpHzYhH3OmbY"
LLM_MODEL = "deepseek-v4-flash"


def call_llm(system_prompt, user_prompt, temperature=0.0):
    """Call OpenAI-compatible LLM API."""
    payload = {
        "model": LLM_MODEL,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    url = LLM_BASE_URL.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    request = urllib.request.Request(url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}",
            "User-Agent": "Mozilla/5.0 (compatible; YamiboBot/1.0)",
        },
        method="POST",
    )
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=180) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


SYSTEM_PROMPT = """你批量总结论坛帖子，返回纯JSON数组。
每条总结严格30-50字，不能用双引号（用单引号），不能出现'楼主''回复者''帖主''大家'等标签词。
每项格式: "tid: 总结内容"
直接输出JSON数组，不要任何其他文字。"""


def get_empty_files():
    """Find all files with empty ai_summary field using rg."""
    import subprocess
    result = subprocess.run(
        ["rg", "-l", 'ai_summary: ""', str(DATA_DIR)],
        capture_output=True, text=True, timeout=120,
    )
    files = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        p = Path(line)
        if p.stem == "00000000":
            continue
        files.append(p)
    return sorted(files)


def extract_frontmatter(content: str):
    """Extract tid, title from YAML frontmatter."""
    tid = ""
    title = ""
    for line in content.split("\n")[:15]:
        if line.startswith("tid:"):
            tid = line.split(":", 1)[1].strip()
        elif line.startswith('title: "'):
            title = line.split('"', 2)[1]
    return tid, title


def update_ai_summary(filepath: Path, summary: str) -> bool:
    """Update the ai_summary field in a YAML frontmatter."""
    content = filepath.read_text(encoding="utf-8")
    safe = summary.replace('"', "'")
    new_content = re.sub(
        r'^ai_summary: ""',
        f'ai_summary: "{safe}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if new_content == content:
        return False
    filepath.write_text(new_content, encoding="utf-8")
    return True


def main():
    print(f"LLM: {LLM_MODEL}", flush=True)

    files = get_empty_files()
    total = len(files)
    print(f"Found {total} files with empty ai_summary", flush=True)

    if total == 0:
        print("All done!", flush=True)
        return

    # Resume from checkpoint
    start_idx = 0
    if CHECKPOINT_PATH.exists():
        try:
            start_idx = int(CHECKPOINT_PATH.read_text().strip())
            if start_idx > 0:
                print(f"Resuming from index {start_idx}", flush=True)
        except (ValueError, OSError):
            pass

    processed = 0
    errors = 0
    BATCH = 10  # files per LLM call

    i = start_idx
    while i < total:
        batch = files[i:i + BATCH]
        print(f"\n[{i//BATCH + 1}/{(total-1)//BATCH + 1}] files {i+1}-{min(i+BATCH, total)}", flush=True)

        # Build the batch request
        entries = []
        tid_map = {}
        for fp in batch:
            content = fp.read_text(encoding="utf-8")
            tid, title = extract_frontmatter(content)
            # Take first ~800 chars of content for summary
            body = content[content.index("---", 3)+3:].strip()[:800]
            tid_map[tid] = fp
            entries.append(f"[tid={tid}] 标题: {title}\n内容: {body}")

        user_prompt = "为以下帖子各生成一条30-50字的总结，返回JSON数组:\n\n" + "\n---\n".join(entries)

        try:
            resp = call_llm(SYSTEM_PROMPT, user_prompt)
            # Parse JSON from response
            resp_clean = resp.strip()
            if resp_clean.startswith("```json"):
                resp_clean = resp_clean.split("```json", 1)[1]
            if resp_clean.startswith("```"):
                resp_clean = resp_clean.split("```", 1)[1]
            if resp_clean.endswith("```"):
                resp_clean = resp_clean.rsplit("```", 1)[0]
            resp_clean = resp_clean.strip()

            results = json.loads(resp_clean)
            ok_count = 0
            for item in results:
                if isinstance(item, str):
                    # Format: "tid: summary"
                    parts = item.split(": ", 1)
                    if len(parts) == 2:
                        tid, summary = parts
                    else:
                        errors += 1
                        continue
                elif isinstance(item, dict):
                    tid = item.get("tid", "")
                    summary = item.get("summary", "")
                else:
                    errors += 1
                    continue

                tid = tid.strip()
                summary = summary.strip()
                n = len(summary)

                if n < 30 or n > 50:
                    print(f"  tid {tid}: {n} chars (out of range, using anyway): {summary[:50]}", flush=True)

                fp = tid_map.get(tid)
                if fp and update_ai_summary(fp, summary):
                    ok_count += 1
                    processed += 1
                else:
                    errors += 1

            print(f"  -> {ok_count}/{len(batch)} OK", flush=True)

        except Exception as e:
            print(f"  ERROR in batch: {e}", flush=True)
            # Fallback: process files individually
            for fp in batch:
                try:
                    content = fp.read_text(encoding="utf-8")
                    tid, title = extract_frontmatter(content)
                    body = content[content.index("---", 3)+3:].strip()[:800]
                    single_prompt = f"为tid={tid} '{title}'生成30-50字总结:\n{body}"
                    s = call_llm("严格30-50字，不用双引号和'楼主回复者'标签", single_prompt)
                    s = s.strip()[:50]
                    if len(s) < 30:
                        errors += 1
                        continue
                    if update_ai_summary(fp, s):
                        processed += 1
                except Exception as e2:
                    print(f"    Fallback error {fp.name}: {e2}", flush=True)
                    errors += 1
            errors += 1

        # Save checkpoint
        i += len(batch)
        CHECKPOINT_PATH.write_text(str(i))

    print(f"\n{'='*40}", flush=True)
    print(f"Done: {processed} processed, {errors} errors", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Run again to resume.", flush=True)
        sys.exit(0)
