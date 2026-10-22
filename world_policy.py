"""Selection policy only; this module never authors or edits generated world laws."""
import re


def recover_from_archive(job, snapshot):
    if not snapshot.get('simulation_error'):
        return None
    for branch in reversed(snapshot.get('archive', [])):
        if branch.get('accepted') or branch.get('parent') != snapshot.get('version'):
            continue
        name = branch.get('code_file', '')
        if not re.fullmatch(r'candidate-[0-9]{6,}\.py', name):
            continue
        try:
            code = (job / name).read_text()
        except FileNotFoundError:
            continue
        if code[:8000] == (snapshot.get('last_failed_code') or '')[:8000]:
            continue
        return {'name': branch['name'], 'hypothesis': branch['hypothesis'],
                'question': branch['question'], 'code': code}
    return None


def accept_candidate(changed, snapshot, novelty, attempt):
    # Call only AFTER sandbox validation and continuity checks. Repair is not novelty.
    return bool(changed and (not snapshot.get('code') or snapshot.get('simulation_error')
                            or novelty >= 0.012 or attempt % 4 == 0))
