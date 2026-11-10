"""Configurable behavior-based grader. Execute only trusted student submissions."""
from student_names import extract_student_name
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import io
from collections import Counter
from pathlib import Path

MAX_ZIP_BYTES = 12 * 1024 * 1024
MAX_UNPACKED_BYTES = 35 * 1024 * 1024


def unpack_zip(data, destination):
    if len(data) > MAX_ZIP_BYTES:
        raise ValueError('ZIP exceeds 12 MB')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        total = 0
        entries = archive.infolist()
        for entry in entries:
            path = Path(entry.filename)
            if path.is_absolute() or '..' in path.parts or entry.is_dir() and len(path.parts) > 30:
                raise ValueError('Unsafe ZIP path')
            if entry.file_size > MAX_UNPACKED_BYTES:
                raise ValueError('Oversized file in ZIP')
            total += entry.file_size
            if total > MAX_UNPACKED_BYTES or len(path.parts) > 30 or len(entries) > 1500:
                raise ValueError('ZIP exceeds extraction limits')
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Symlinks are not supported')
        archive.extractall(destination)


def find_script(root, question):
    matches = [p for p in root.rglob('*.py')
               if p.name.lower().endswith(question.lower() + '.py')
               and (p.stem.lower() == question.lower() or p.stem[-len(question) - 1] in ' _-')
               and '__MACOSX' not in p.parts
               and not any(part.startswith('.') for part in p.relative_to(root).parts)]
    if not matches:
        return None
    return sorted(matches, key=lambda p: (p.stem.lower() != question.lower(),
                                          len(p.relative_to(root).parts), str(p).lower()))[0]


def canonical_script_name(filename):
    """Return the standard q*.py name for a prefixed LMS submission filename."""
    stem = Path(filename).stem
    match = re.search(r'(?:^|[\s_-])(q\d+[a-z]?)$', stem, re.IGNORECASE)
    return match.group(1).lower() + '.py' if match else filename


def run_python(work, code, timeout=4):
    cmd = [sys.executable, '-I', '-c', code]
    try:
        def limits():
            if os.name != 'nt':
                import resource
                resource.setrlimit(resource.RLIMIT_CPU, (3, 4))
                resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
                resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024))
        proc = subprocess.run(cmd, cwd=work, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout,
                              preexec_fn=limits if os.name != 'nt' else None,
                              env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        return proc.returncode, proc.stdout[-15000:], proc.stderr[-1500:]
    except subprocess.TimeoutExpired:
        return -1, '', 'Execution timed out'


def normalize_output(text):
    return '\n'.join(line.rstrip() for line in text.replace('\r\n', '\n').strip().splitlines())


def normalize_transactions(text, grouped=False):
    if not grouped:
        return sorted(line.strip() for line in text.splitlines() if line.strip())
    groups = {}
    month = None
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.match(r'^\s*(\d\d-\d{4}):\s*total transaction amount is\s*\$([\d.]+)', line)
        if match:
            month = match.group(1)
            groups[month] = {'total': float(match.group(2)), 'items': []}
        elif month:
            groups[month]['items'].append(tuple(x.strip() for x in line.split('\t') if x.strip()))
    return groups


def compare(expected, actual, mode, starter):
    if mode == 'transactions':
        return normalize_transactions(expected) == normalize_transactions(actual)
    if mode == 'grouped_transactions':
        expected_groups = normalize_transactions(expected, True)
        actual_groups = normalize_transactions(actual, True)
        return (expected_groups.keys() == actual_groups.keys()
                and all(abs(expected_groups[key]['total'] - actual_groups[key]['total']) < 0.011
                        and Counter(expected_groups[key]['items']) == Counter(actual_groups[key]['items'])
                        for key in expected_groups))
    if mode == 'q3_alternative_blocks':
        expected_lines = [line.strip() for line in normalize_output(expected).splitlines() if line.strip()]
        actual_lines = [line.strip() for line in normalize_output(actual).splitlines() if line.strip()]
        return (actual_lines == expected_lines or actual_lines == expected_lines[:3]
                or actual_lines == expected_lines[3:6] or actual_lines == expected_lines[6:9])
    if mode == 'scrambled_words':
        source = (starter / 'talk.txt').read_text(errors='replace').split()
        output = actual.split()

        def valid(original, candidate):
            punctuation = ",'!."
            if original and original[-1] in punctuation:
                return candidate.endswith(original[-1]) and valid(original[:-1], candidate[:-1])
            return (len(original) == len(candidate)
                    and (len(original) < 2 or (original[0] == candidate[0] and original[-1] == candidate[-1]))
                    and Counter(original[1:-1]) == Counter(candidate[1:-1]))

        return len(source) == len(output) and all(valid(a, b) for a, b in zip(source, output))
    if mode == 'dictionary_pairs':
        try:
            obj = ast.literal_eval(actual.strip())
            return sorted((str(key), int(value)) for key, value in obj) == sorted(
                (str(key), int(value)) for key, value in ast.literal_eval(expected.strip()))
        except Exception:
            return normalize_output(expected) == normalize_output(actual)
    if mode == 'sorted_lines':
        return sorted(line.strip() for line in expected.splitlines() if line.strip()) == sorted(
            line.strip() for line in actual.splitlines() if line.strip())
    if mode == 'json_lines':
        try:
            return [json.loads(line) for line in expected.splitlines() if line.strip()] == [
                json.loads(line) for line in actual.splitlines() if line.strip()]
        except Exception:
            return normalize_output(expected) == normalize_output(actual)
    return normalize_output(expected) == normalize_output(actual)


def make_runner(question, spec, *, student=False):
    function_name = spec.get('function')
    source_name = question + '.py'
    if function_name:
        cases = spec.get('cases', [])
        if spec.get('runner') == 'function_definitions':
            loader = (f"import ast\nsrc=open({source_name!r},encoding='utf-8-sig').read()\n"
                      "tree=ast.parse(src)\n"
                      "tree.body=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.Import,ast.ImportFrom,ast.ClassDef))]\n"
                      f"ns={{'__name__':'grading_module'}}\nexec(compile(tree,{source_name!r},'exec'),ns)\nf=ns[{function_name!r}]\n")
        else:
            loader = ("import importlib.util,contextlib,io\n"
                      f"s=importlib.util.spec_from_file_location('student_module',{source_name!r})\n"
                      "m=importlib.util.module_from_spec(s)\n"
                      "with contextlib.redirect_stdout(io.StringIO()):s.loader.exec_module(m)\n"
                      f"f=getattr(m,{function_name!r})\n")
        return ("import json,contextlib,io,sys\nsys.path.insert(0,'.')\n" + loader
                + f"for args in {cases!r}:\n b=io.StringIO()\n with contextlib.redirect_stdout(b): result=f(*args)\n"
                  " print(json.dumps({'result':result,'printed':b.getvalue()},sort_keys=True,default=str))\n")

    if student and spec.get('student_runner') == 'script_then_call':
        call = spec['student_call']
        return (f"from pathlib import Path\np=Path({source_name!r}); ns={{'__name__':'__main__'}}\n"
                f"exec(compile(p.read_text(encoding='utf-8-sig'),{source_name!r},'exec'),ns)\n"
                f"f=ns.get({call['function']!r})\nif callable(f):f(*{call.get('args', [])!r})")
    return (f"from pathlib import Path\nexec(compile(Path({source_name!r}).read_text(encoding='utf-8-sig'),"
            f"{source_name!r},'exec'),{{'__name__':'__main__','__file__':{source_name!r}}})")


def make_input(spec, starter):
    mode = spec.get('input_mode')
    if mode == 'first_facility':
        row = (starter / spec.get('input_file', 'facilities.txt')).read_text().splitlines()[0].split('\t')
        return f'Y\n{row[0]}\n{row[1]}\nN\n'
    if mode in ('first_school', 'first_school_with_count'):
        school = (starter / spec.get('input_file', 'facilities.txt')).read_text().splitlines()[0].split('\t')[0]
        count = f"{spec.get('count', 30)}\n" if mode == 'first_school_with_count' else ''
        return f'Y\n{school}\n{count}N\n'
    return spec.get('input')


def run_with_input(work, code, input_text=None, timeout=4):
    if input_text is not None:
        prefix = ("import builtins\n_inputs=iter(" + repr(input_text.splitlines()) + ")\n"
                  "builtins.input=lambda prompt='': (print(prompt,end=''),next(_inputs))[1]\n")
        code = prefix + code
    return run_python(work, code, timeout)


def grade_question(root, question, starter, solutions, spec, default_timeout=4):
    script = find_script(root, question)
    if not script:
        return {'status': 'Missing', 'score': 0, 'detail': f'{question}.py not found'}

    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        student = base / 'student'
        reference = base / 'reference'
        student.mkdir()
        reference.mkdir()
        starter_extensions = {ext.lower() for ext in spec.get('starter_extensions', ['.txt'])}
        starter_names = set(spec.get('starter_names', []))
        for work in (student, reference):
            for path in starter.iterdir():
                if path.is_file() and (path.suffix.lower() in starter_extensions or path.name in starter_names):
                    shutil.copy2(path, work / path.name)

        for path in script.parent.iterdir():
            if path.is_file() and path.suffix.lower() == '.py' and path.stat().st_size < 300000:
                shutil.copy2(path, student / canonical_script_name(path.name))
        shutil.copy2(script, student / (question + '.py'))
        for path in solutions.glob('*.py'):
            shutil.copy2(path, reference / path.name)

        for work in (student, reference):
            if spec.get('output_file'):
                (work / spec['output_file']).unlink(missing_ok=True)

        student_code = make_runner(question, spec, student=True)
        reference_code = make_runner(question, spec)
        input_text = make_input(spec, starter)
        timeout = spec.get('timeout', default_timeout)
        ref_code, ref_output, ref_error = run_with_input(reference, reference_code, input_text, timeout)
        if ref_code:
            return {'status': 'Reference error', 'score': 0, 'detail': ref_error or 'Reference failed'}
        stu_code, stu_output, stu_error = run_with_input(student, student_code, input_text, timeout)
        if stu_code:
            return {'status': 'Error', 'score': 0, 'detail': stu_error or 'Student program exited unsuccessfully'}

        output_file = spec.get('output_file')
        if output_file:
            expected_path = reference / output_file
            actual_path = student / output_file
            expected = expected_path.read_text(errors='replace') if expected_path.exists() else None
            actual = actual_path.read_text(errors='replace') if actual_path.exists() else None
            if actual is None:
                return {'status': 'Failed', 'score': 0, 'detail': f'Missing output file: {output_file}'}
            if expected is None:
                return {'status': 'Reference error', 'score': 0, 'detail': 'Reference output not generated'}
        else:
            expected, actual = ref_output, stu_output

        passed = compare(expected, actual, spec.get('compare', 'exact'), starter)
        return {'status': 'Passed' if passed else 'Failed', 'score': int(passed),
                'detail': 'Behavior matched reference' if passed else 'Behavior differs from reference',
                'expected': str(expected)[:500], 'actual': str(actual)[:500]}


def workspace_folder_name(student):
    """Make a student label safe to use as a directory name on Windows."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', student).strip(' .')
    if not name or name in {'.', '..'}:
        name = 'untitled'
    if name.split('.')[0].upper() in {
            'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
            *(f'LPT{i}' for i in range(1, 10))}:
        name = f'student_{name}'
    return name[:100].rstrip(' .') or 'untitled'


def create_student_workspace(workspace, student):
    """Create a unique persistent folder for one uploaded student ZIP."""
    base_name = workspace_folder_name(student)
    candidate = workspace / base_name
    suffix = 2
    while True:
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            candidate = workspace / f'{base_name} ({suffix})'
            suffix += 1


def grade_all(uploads, selected, starter, solutions, settings, workspace):
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    results = []
    for filename, data in uploads:
        student_name = extract_student_name(filename)
        row = {'student': student_name, 'filename': filename,
               'questions': {}, 'total': 0, 'max': len(selected)}
        try:
            root = create_student_workspace(workspace, student_name)
            unpack_zip(data, root)
            row['workspace_folder'] = root.name
            for question in selected:
                spec = {key: settings[key] for key in ('starter_extensions', 'starter_names')
                        if key in settings}
                spec.update(settings['questions'][question])
                item = grade_question(root, question, starter, solutions,
                                      spec, settings.get('timeout', 4))
                row['questions'][question] = item
                row['total'] += item['score']
        except Exception as exc:
            for question in selected:
                row['questions'][question] = {'status': 'Error', 'score': 0, 'detail': str(exc)[:250]}
        results.append(row)
    return results
