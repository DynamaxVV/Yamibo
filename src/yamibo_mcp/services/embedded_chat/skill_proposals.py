"""Reviewed, versioned overrides of packaged project guidance (never executable code)."""
from __future__ import annotations

import difflib
import fcntl
import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .project_skills import SKILLS, read_skill


class SkillProposals:
    def __init__(self, settings):
        self.root = Path(settings.data_dir) / 'agent' / 'skill-reviews'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if any(p.is_symlink() for p in (self.root, *self.root.parents)):
            raise ValueError('UNSAFE_SKILL_DIRECTORY')

    @contextmanager
    def state(self):
        fd = os.open(self.root / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            path = self.root / 'state.json'
            if path.is_symlink():
                raise ValueError('UNSAFE_SKILL_STATE')
            state = json.loads(path.read_text()) if path.exists() else {'versions': {}, 'proposals': {}}
            yield state
            temp = self.root / (uuid.uuid4().hex + '.tmp')
            try:
                with temp.open('x', encoding='utf-8') as out:
                    json.dump(state, out, ensure_ascii=False)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)
        finally:
            os.close(fd)

    @staticmethod
    def current(state, name):
        if name not in SKILLS:
            raise ValueError('INVALID_PROJECT_SKILL')
        versions = state['versions'].get(name, [])
        if versions:
            return versions[-1]
        content = read_skill(name, chunk_size=100000)['content']
        return {'content': content, 'revision': hashlib.sha256(content.encode()).hexdigest()}

    def read(self, name):
        with self.state() as state:
            return dict(self.current(state, name))

    def propose(self, name, content, reason, expected_revision, *, run_id=None):
        if not content.strip() or len(content) > 32000 or not reason.strip() or len(reason) > 2000:
            raise ValueError('INVALID_SKILL_PROPOSAL')
        with self.state() as state:
            current = self.current(state, name)
            if expected_revision != current['revision']:
                raise ValueError('SKILL_REVISION_CONFLICT')
            if content == current['content']:
                raise ValueError('SKILL_UNCHANGED')
            proposal = {'id': uuid.uuid4().hex, 'name': name, 'content': content,
                        'reason': reason, 'base_revision': expected_revision,
                        'status': 'pending', 'created_at': time.time(), 'run_id': run_id,
                        'diff': ''.join(difflib.unified_diff(current['content'].splitlines(True), content.splitlines(True), fromfile='current', tofile='proposed'))}
            state['proposals'][proposal['id']] = proposal
            return dict(proposal)

    def list(self):
        with self.state() as state:
            return list(state['proposals'].values())

    def review(self, proposal_id, *, approve):
        with self.state() as state:
            proposal = state['proposals'][proposal_id]
            if proposal['status'] != 'pending':
                raise ValueError('SKILL_PROPOSAL_ALREADY_REVIEWED')
            if approve:
                current = self.current(state, proposal['name'])
                if current['revision'] != proposal['base_revision']:
                    raise ValueError('SKILL_REVISION_CONFLICT')
                version = {'content': proposal['content'], 'revision': uuid.uuid4().hex,
                           'proposal_id': proposal_id, 'approved_at': time.time()}
                state['versions'].setdefault(proposal['name'], []).append(version)
            proposal['status'] = 'approved' if approve else 'rejected'
            proposal['reviewed_at'] = time.time()
            return dict(proposal)
