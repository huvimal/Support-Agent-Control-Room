# Support Agent Control Room

Local FastAPI prototype for a Vietnamese refund workflow with human approval,
persistent state, and recovery after an interrupted tool call. It uses
fictional orders and a simulated payment ledger.

## Run locally on Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8507
```

Open http://127.0.0.1:8507/docs and send this body to **POST /requests**:

```json
{"message":"Tôi đã trả hàng và muốn hoàn tiền cho đơn VN1001."}
```

Copy `case.id`. The case starts in `awaiting_approval`. VN1001 and VN1003
are eligible test orders. VN1002 has shipped and is ineligible. Each order
has at most one case in this prototype.

## Approve and simulate a failure

In a second PowerShell window in the project folder:

```powershell
$caseId = Read-Host "Case ID"
.\.venv\Scripts\python.exe manage.py worker
.\.venv\Scripts\python.exe manage.py review $caseId --decision approve --note "Verified returned demo order"
.\.venv\Scripts\python.exe manage.py worker --interrupt-after-refund
$LASTEXITCODE
.\.venv\Scripts\python.exe manage.py inspect $caseId
```

The first worker command should return `no_approved_cases` in a fresh demo.
The fault switch deliberately exits with 75 after the simulated provider
commits a refund. Inspect now shows an approved case with a null case
transaction ID and a ledger entry with a real demo transaction ID.

## Recover

```powershell
.\.venv\Scripts\python.exe manage.py worker
.\.venv\Scripts\python.exe manage.py inspect $caseId
.\.venv\Scripts\python.exe manage.py worker
```

The retry should return `reused_ledger_entry: true` and the same transaction ID.
The case becomes `completed`. A further worker invocation returns
`no_approved_cases` when the demo has no other approvals. Use GET
`/cases/{case_id}` to inspect the same state and event history in Swagger.

The workflow and ledger use separate SQLite files. The simulated provider
deduplicates by case and order before committing. Worker recovery relies on
that behavior. An actual provider integration would need equivalent
idempotency and reconciliation semantics. The audit events record workflow
attempts and completion, while the ledger stores the simulated side effect.

## Try AI intent classification

Set GROQ_API_KEY in the server terminal before starting Uvicorn to enable
Groq JSON mode for intent classification. Without a key, intake uses local
rules. Use fictional messages. The model can choose an intent. Python extracts
the order ID from the message, derives the amount from the order database,
and handles approval separately through the local operator CLI.

## Verify

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests cover rejection and approval prerequisites, retry after interruption,
duplicate intake, and two workers competing on the same case. Test databases
are temporary. For a new manual run, use VN1003 or set both
SUPPORT_WORKFLOW_DB and SUPPORT_LEDGER_DB to fresh file paths in both
terminals before starting the server and worker.

This is a single-machine demo with an operator CLI and no account or order
authentication. It is designed for localhost and uses no payment integration.
