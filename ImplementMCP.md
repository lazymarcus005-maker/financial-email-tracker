# Implement MCP for Financial Email Tracker

เอกสารนี้ประเมินและวางแผนเพิ่ม Model Context Protocol (MCP) ให้ระบบ `financial-email-tracker` เพื่อให้ AI agent สามารถอ่านสรุปการเงิน, ค้นหารายการ, ดูรายการที่ parse ไม่ได้, และสั่ง action บางอย่างได้อย่างปลอดภัย

สถานะปัจจุบัน: ยังไม่มี MCP integration ใน repo นี้ แต่โครงสร้างระบบพร้อมต่อ MCP ได้ดี เพราะ business logic หลักแยกอยู่ใน module ที่ reuse ได้ เช่น `app.storage.queries`, `app.ingestion.service`, `app.integrations.line`, และ `app.parsers.registry`

## เป้าหมาย

1. ให้ AI agent ต่อเข้าระบบผ่าน MCP ได้
2. เปิดเครื่องมือ read-only ก่อน เพื่อลดความเสี่ยงข้อมูลการเงิน
3. บังคับ owner/user scope ทุกครั้ง ห้าม agent เห็นข้อมูลข้าม user
4. เพิ่ม write/action tools เฉพาะเมื่อมี permission model ชัดเจน
5. รองรับทั้ง local agent แบบ stdio และต่อยอดเป็น remote MCP ผ่าน HTTP ได้ภายหลัง

## แนวทางที่แนะนำ

เริ่มจาก MCP server แยก process แบบ stdio:

```text
AI Agent
  |
  | MCP stdio
  v
app/mcp/server.py
  |
  +-- app.storage.queries
  +-- app.storage.database
  +-- app.ingestion.service
  +-- app.integrations.line
```

เหตุผล:

- ไม่กระทบ FastAPI web app เดิม
- ไม่ต้องผูกกับ cookie/session middleware ของเว็บ
- ใช้งานกับ local AI agent ได้ง่าย
- deploy/test/debug ง่ายกว่า remote MCP
- ลด blast radius ถ้ามี bug ใน MCP layer

หลังจาก read-only MVP เสถียรแล้ว ค่อยเพิ่ม write tools และ/หรือ mount MCP เข้า FastAPI เป็น `/mcp`

## Dependency

เพิ่ม MCP Python SDK ใน `requirements.txt`

```text
mcp>=1.27,<2
```

หมายเหตุ: MCP Python SDK v2 มีเอกสารใหม่และมี `FastMCP` API ที่ดีขึ้น แต่ควร pin production line ให้ชัดเจนก่อน ถ้าจะใช้ v2 ควรทำ branch migration แยก

## Config ที่ควรเพิ่ม

เพิ่ม env/config สำหรับ MCP โดยเฉพาะ:

```yaml
MCP_ENABLED: false
MCP_OWNER_USER_ID: null
MCP_ALLOW_WRITE: false
MCP_EXPOSE_RAW_EMAIL: false
MCP_API_TOKEN: null
```

ความหมาย:

- `MCP_ENABLED`: เปิด/ปิด MCP server หรือ mount
- `MCP_OWNER_USER_ID`: user id ที่ MCP จะทำงานแทนใน local mode
- `MCP_ALLOW_WRITE`: อนุญาต tools ที่แก้ข้อมูล เช่น update category, ignore, mapping
- `MCP_EXPOSE_RAW_EMAIL`: อนุญาตให้ agent เห็น raw email body หรือไม่
- `MCP_API_TOKEN`: ใช้ตอนทำ remote HTTP MCP

สำหรับ MVP แบบ stdio อาจเริ่มด้วย `MCP_OWNER_USER_ID` และ `MCP_ALLOW_WRITE` ก่อนก็พอ

## Proposed Files

```text
app/
  mcp/
    __init__.py
    server.py          # MCP tool definitions
    security.py        # owner scope + permission helpers
    schemas.py         # Pydantic schemas สำหรับ tool output

tests/
  test_mcp_server.py
  test_mcp_security.py

docs/
  mcp-agent-config.md  # ตัวอย่าง config สำหรับ agent แต่ละตัว
```

ถ้าต้องการทำ MVP ให้เล็กที่สุด:

```text
app/mcp/__init__.py
app/mcp/server.py
tests/test_mcp_server.py
```

## Phase 1: Read-Only MVP

เปิด tools ที่ไม่แก้ข้อมูลก่อน

### `get_dashboard_summary`

สรุปภาพรวมของ user:

- รายรับวันนี้
- รายจ่ายวันนี้
- จำนวน transaction ทั้งหมด
- จำนวน uncategorized
- จำนวน parse error
- last sync

ใช้ helper เดิม:

- `queries.get_dashboard_stats(db, owner_user_id=...)`

### `search_transactions`

ค้นหาและ filter รายการธุรกรรม:

Input:

```json
{
  "date_from": "2026-07-01",
  "date_to": "2026-07-28",
  "category": "Food",
  "transaction_type": null,
  "direction": "out",
  "search": "shopee",
  "page": 1,
  "page_size": 20
}
```

ใช้ helper เดิม:

- `queries.list_transactions(...)`

ข้อจำกัดที่ควร enforce:

- `page_size <= 50` สำหรับ MCP แม้ web API รองรับถึง 100
- default sort เป็น `occurred_at DESC`
- ไม่ส่ง `raw_fields` ถ้าไม่จำเป็น

### `get_transaction_detail`

ดูรายละเอียด transaction ตาม id

ใช้ helper เดิม:

- `queries.get_transaction(db, transaction_id, owner_user_id=...)`

ข้อควรระวัง:

- ต้องส่ง `owner_user_id` ทุกครั้ง
- ถ้าไม่เจอให้ return structured error ไม่ใช่ expose exception

### `list_unknown_emails`

ดูรายการ email ที่ parse ไม่ได้

ใช้ helper เดิม:

- `queries.list_unknown(...)`

ข้อควรระวัง:

- ห้ามส่ง raw email body ใน phase 1
- ส่งเฉพาะ subject, sender, warnings, amount, received_at, status

### `list_category_mappings`

ดู rule mapping ปัจจุบัน

ใช้ helper เดิม:

- `queries.list_mappings(db, owner_user_id=...)`

### `get_daily_summary`

ดูข้อความ summary แบบเดียวกับที่ส่ง LINE โดยยังไม่ส่งจริง

ใช้ helper เดิม:

- `queries.get_daily_summary_data(...)`
- `line.format_daily_summary(data)`

Output ควรมีทั้ง structured data และ formatted text:

```json
{
  "data": {},
  "line_text": "..."
}
```

## Phase 2: Safe Write Tools

เพิ่มเมื่อ read-only MVP ใช้งานได้แล้ว และมี `MCP_ALLOW_WRITE=true`

### `update_transaction_category`

แก้หมวดหมู่ transaction

ใช้ helper เดิม:

- `queries.update_transaction_category(...)`
- `classification.history.record(...)`

Guard:

- require `MCP_ALLOW_WRITE=true`
- require `owner_user_id`
- category ต้องไม่ว่าง
- บันทึก audit log

### `ignore_transaction`

ตั้ง transaction เป็น ignored/unignored

ใช้ helper เดิม:

- `queries.set_transaction_ignored(...)`

Guard:

- require `MCP_ALLOW_WRITE=true`
- return transaction หลัง update

### `create_category_mapping`

สร้าง counterparty mapping

ใช้ helper เดิม:

- `queries.create_mapping(...)`

Guard:

- require `MCP_ALLOW_WRITE=true`
- counterparty/category ต้องไม่ว่าง
- จำกัดความยาว input

### `run_ingestion`

สั่งดึง Gmail ใหม่

ใช้ helper เดิม:

- `ingestion.service.run_ingestion(...)`

Guard:

- require `MCP_ALLOW_WRITE=true`
- require Gmail token ของ user นั้น
- ใช้ lock เดิม `_INGESTION_LOCK`
- จำกัด window เช่น `default`, `last_7_days`, `last_30_days`
- ห้ามรับ Gmail query arbitrary จาก agent ในช่วงแรก

### `send_line_daily_summary`

ส่ง summary เข้า LINE จริง

Guard:

- ควรแยก flag เช่น `MCP_ALLOW_SEND=true`
- ต้อง return preview ก่อนใน UX ของ agent ถ้า agent รองรับ confirmation
- ถ้าเป็น local agent ที่ไม่มี confirmation layer ไม่ควรเปิด tool นี้เป็น default

## Phase 3: Remote MCP ผ่าน FastAPI

ถ้าต้องการให้ agent ภายนอกต่อเข้าระบบ ให้ mount MCP เป็น HTTP endpoint:

```text
FastAPI app
  |
  +-- /api/*
  +-- /mcp
```

สิ่งที่ต้องทำเพิ่ม:

1. เพิ่ม token auth สำหรับ `/mcp`
2. แยก middleware MCP ออกจาก web session cookie
3. map token ไปยัง `owner_user_id`
4. เพิ่ม rate limit
5. เพิ่ม audit log ทุก tool call
6. deploy หลัง HTTPS เท่านั้น

Remote MCP ไม่ควรใช้ `MCP_OWNER_USER_ID` แบบ global ใน production เพราะเสี่ยงข้อมูลข้าม user ควรใช้ token ต่อ user หรือ token ต่อ integration

## Security Requirements

### Owner Scope

ทุก tool ต้อง resolve `owner_user_id` ก่อนเรียก query

ห้ามทำแบบนี้:

```python
await queries.list_transactions(db)
```

ต้องทำแบบนี้:

```python
await queries.list_transactions(db, owner_user_id=owner_user_id)
```

เหตุผล: ระบบมี multi-user data isolation อยู่แล้ว ถ้า MCP ไม่ส่ง owner scope จะเสี่ยงเห็นข้อมูลรวมทุก user

### Raw Email

ไม่ควร expose raw email body เป็น default เพราะมีข้อมูลส่วนตัวสูง

ถ้าจะเปิด:

- ต้องใช้ `MCP_EXPOSE_RAW_EMAIL=true`
- จำกัดเฉพาะ transaction id ที่ owner เห็นได้
- mask account number / reference / email address เท่าที่ทำได้

### Write Permission

ทุก write/action tool ต้องผ่าน guard:

```python
if not settings.MCP_ALLOW_WRITE:
    raise PermissionError("MCP write tools are disabled")
```

### Audit Log

ควร log:

- tool name
- owner_user_id
- arguments แบบ masked
- success/failure
- duration

ห้าม log:

- LINE token
- Gmail token
- raw email body
- password/session secret

## Example Tool Design

ตัวอย่าง contract สำหรับ tools:

```text
get_dashboard_summary() -> DashboardSummary
search_transactions(filters) -> TransactionSearchResult
get_transaction_detail(transaction_id) -> TransactionDetail
list_unknown_emails(status, page, page_size) -> UnknownEmailResult
list_category_mappings() -> Mapping[]
get_daily_summary(day) -> DailySummaryPreview
```

Write tools:

```text
update_transaction_category(transaction_id, category) -> TransactionDetail
ignore_transaction(transaction_id, ignored) -> TransactionDetail
create_category_mapping(counterparty, category) -> Mapping
run_ingestion(window) -> IngestionSummary
send_line_daily_summary(day) -> SendResult
```

## Testing Plan

ควรมี test อย่างน้อย:

1. MCP read tools return เฉพาะข้อมูลของ `owner_user_id`
2. `search_transactions` จำกัด `page_size`
3. `get_transaction_detail` ไม่เห็น transaction ของ user อื่น
4. write tools fail เมื่อ `MCP_ALLOW_WRITE=false`
5. write tools สำเร็จเมื่อ `MCP_ALLOW_WRITE=true`
6. `get_daily_summary` format ตรงกับ LINE summary logic
7. `run_ingestion` ไม่รับ arbitrary Gmail query
8. ไม่มี secret/raw token หลุดใน output

## Implementation Steps

1. เพิ่ม dependency `mcp`
2. เพิ่ม MCP config ใน `Settings`
3. สร้าง `app/mcp/server.py`
4. ทำ helper resolve owner user:

```python
def get_mcp_owner_user_id(settings: Settings) -> int:
    if settings.MCP_OWNER_USER_ID is None:
        raise RuntimeError("MCP_OWNER_USER_ID is required")
    return int(settings.MCP_OWNER_USER_ID)
```

5. implement read-only tools
6. เพิ่ม unit tests
7. เพิ่ม docs วิธี run local MCP
8. ทดสอบกับ local agent
9. เพิ่ม write tools แบบปิด default
10. พิจารณา remote MCP หลังจาก MVP ใช้งานจริง

## Local Run Example

ตัวอย่าง command สำหรับ agent config:

```json
{
  "mcpServers": {
    "financial-email-tracker": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "env": {
        "MCP_OWNER_USER_ID": "1",
        "MCP_ALLOW_WRITE": "false",
        "DATABASE_PATH": "data/finance.db"
      }
    }
  }
}
```

หมายเหตุ: key/config format จริงขึ้นกับ agent ที่ใช้

## Recommended MVP Scope

รอบแรกควรทำแค่นี้:

- `get_dashboard_summary`
- `search_transactions`
- `get_transaction_detail`
- `list_unknown_emails`
- `get_daily_summary`

ยังไม่ควรเปิด:

- raw email body
- delete transaction
- clear/import/export runtime data
- send LINE จริง
- arbitrary SQL
- arbitrary Gmail query

## Open Questions

1. MCP จะใช้กับ agent ตัวไหนเป็นหลัก เช่น Claude Desktop, Cursor, Codex, custom agent
2. ต้องการใช้ local-only หรือ remote ผ่าน HTTP
3. หนึ่ง agent ผูกกับ user เดียว หรือ agent ต้องสลับ user ได้
4. ต้องการให้ agent แก้ category ได้เลยไหม หรือให้ preview แล้วมนุษย์กดยืนยันใน UI
5. ต้องการส่ง LINE แยกต่อ user ในอนาคตไหม เพราะตอนนี้ summary job ส่งไป `LINE_USER_ID` เดียวจาก config

## Recommendation

ให้เริ่มจาก read-only stdio MCP ก่อน เพราะได้ประโยชน์เร็วและปลอดภัย:

```text
Phase 1: read-only MCP, local stdio
Phase 2: write tools with MCP_ALLOW_WRITE
Phase 3: remote HTTP MCP with token auth
Phase 4: per-user LINE destination / permission model
```

แนวนี้ต่อยอดกับระบบเดิมได้ดี และลดโอกาสทำให้ web app, scheduler, Gmail OAuth, หรือ data isolation เดิมเสียหาย
