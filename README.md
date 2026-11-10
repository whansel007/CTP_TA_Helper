# IS1704 Flask Auto Grader — Lab 7 and Lab 8

## Run (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 . Choose Lab 7 or Lab 8, upload student ZIP files, select question parts, grade, and export CSV.

Uploaded ZIP contents are extracted to the `workspace/` folder in this project, with one folder per student. These folders remain there across grading runs and app restarts. Use **Empty workspace** on the site to delete the extracted student folders.

## Supported questions

- Lab 7: Q1a, Q1b, Q2a, Q2b, Q2c, Q3, Q4a, Q4b, Q5a, Q5b, Q6, Q7.
- Lab 8: Q1a, Q1b, Q2, Q3a, Q3b, Q3c, Q4, Q5a, Q5b, Q5c, Q6.

The shared grader searches recursively for `q*.py` in each ZIP, including LMS-prefixed filenames such as `submission - q1a.py`. It uses the bundled input data and compares behavior with the bundled solution files. Function-based questions use multiple predefined cases where available; interactive questions use a reference-matched scripted input sequence. Each selected part is worth 1 point. This is a preliminary automated correctness score, **not** a full rubric or comprehensive test suite. Review failures and scores before recording grades.

Lab 8 Q6 uses the function `create_employee_dictionary` instead of the interactive employee-search loop; Lab 8 Q3a–c exercise a single interactive search using the first facility in the supplied data. These are limited tests and may not catch all incorrect solutions.

## Security

**Student ZIP files contain executable code.** Run only in an isolated virtual machine or sandbox without network access and without sensitive data. The Flask server is not a security boundary. Resource limits and timeouts are not complete containment, particularly on Windows. Avoid exposing the Flask service to the internet.

## Files

- `app.py`: Flask routes, lab selector, CSV export
- `grader.py`: shared grading engine for both labs
- `settings.json`: lab resources, question lists, test cases, inputs, and comparison modes
- `templates/index.html`: shared interface
- `workspace/`: persistent extracted student submissions; cleared from the site with **Empty workspace**
- `assets/lab7/starter` and `assets/lab7/solutions`: Lab 7 input files and reference implementations
- `assets/lab8/starter` and `assets/lab8/solutions`: Lab 8 input files and reference implementations
