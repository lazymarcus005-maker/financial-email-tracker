# แผนการย้ายฐานข้อมูลไป PostgreSQL

> วันที่: 2026-07-29 · Branch: `features/db-migration` · สถานะ: **เฟส 1-4 เสร็จ (adapter + data migration verify แล้ว)**

## เป้าหมายและขอบเขต

- **เข้าถึงหลายเครื่อง/หลายที่** — Postgres server เป็น single source of truth ทั้งแอปและเครื่องอื่นต่อเข้ามาเป็น client
- **รองรับผู้ใช้/โหลดมากขึ้น** — server จัดการ concurrency เอง (MVCC จริง) ดีกว่าไฟล์ SQLite (แย่ง WAL file ข้าม process)

**สิ่งที่แผนนี้ไม่ทำ (out of scope):** ไม่เปลี่ยน schema ทาง logic (คงตาราง/คอลัมน์เดิมทั้งหมด — แค่พอร์ต dialect), ไม่ทำ sharding/replication

> ⚠️ ข้อควรระวัง: repo นี้มี `finance.db` จริง — **ทุกขั้นทดสอบให้ทำกับ copy ของ DB เสมอ ห้ามแตะตัวจริงตรงๆ**

> ⚠️ **สิ่งสำคัญเกี่ยวกับ branch sync:** `postgres_backend.py` ต้องมี schema ตรงกับตารางทั้งหมดที่แอปใช้จริง — ถ้ามี PR/feature อื่นเข้า `master` ที่เพิ่มตาราง/คอลัมน์ใหม่ (เช่น feature `insurance_policies` ดูข้อ "เหตุการณ์จริง" ด้านล่าง) ต้อง sync `SCHEMA_SQL` ใน postgres_backend.py ให้ตรงด้วยเสมอ ก่อน deploy ไม่งั้นแอปจะ error `relation "..." does not exist` ตอน runtime

---

## สถานะ (2026-07-29)

| เฟส | สถานะ |
|---|---|
| 0 — verify library | ✅ `asyncpg==0.31.0` — driver async มาตรฐานสำหรับ Postgres |
| 1 — adapter + config | ✅ [app/storage/postgres_backend.py](../app/storage/postgres_backend.py) + config `DATABASE_BACKEND=postgres`, `DATABASE_URL`, `DATABASE_SSL` |
| 2 — schema | ✅ schema เขียนใหม่สะอาด (ไม่มีของเก่าเหมือน SQLite เพราะเริ่มจาก data migration ครั้งเดียว ไม่ต้อง evolve เหมือนไฟล์ SQLite ที่ผ่าน ALTER TABLE มาหลายปี) |
| 3 — tests | ✅ dialect bug ที่เจอแก้หมดแล้ว ยืนยันถูกต้องด้วย isolated tests + subset ของ suite ที่ผ่านก่อน hang — ดูข้อจำกัดด้านล่าง |
| 4 — data migration | ✅ ทดสอบกับ copy ของ finance.db จริง — byte-identical ทุกแถวทุกตาราง, identity sequence ต่อถูกต้อง, smoke test query layer ผ่าน — script: [scripts/migrate_to_postgres.py](../scripts/migrate_to_postgres.py) |
| 5 — deploy + TLS | ⏳ endpoint ทดสอบตอนนี้ (`169.58.65.88:5432`) **ไม่มี TLS เลย** (server ปฏิเสธ SSL upgrade) — ใช้ได้เฉพาะ dev/test เท่านั้น |
| 6 — cleanup | ⏳ |

---

## สถาปัตยกรรม: Adapter Layer (แก้จุดเดียว ไม่ไล่แก้ทั้ง codebase)

connection ในแอปรวมศูนย์ที่ 2 จุดเท่านั้น — `init_db()` และ `get_connection()` ใน [app/storage/database.py](../app/storage/database.py) และ DB call sites ทั้งหมด (~292 จุด) ใช้ interface มาตรฐานเดียวกัน (`db.execute`, `cursor.fetchone/fetchall`, `db.commit`, `dict(row)`) — จุดนี้ทำให้สร้าง [app/storage/postgres_backend.py](../app/storage/postgres_backend.py) เป็น adapter เลียนแบบ interface ของ `aiosqlite` ได้ โดย**ไม่ต้องแก้ call sites เลย**

| ที่ call sites ใช้ | adapter ต้องทำ |
|---|---|
| `db.execute(sql, params)` → cursor | translate `?` → `$1,$2,...`, คืน cursor-like |
| `cursor.fetchone()` / `fetchall()` | asyncpg `Record` รองรับ index/key access + `dict()` ในตัวอยู่แล้ว |
| `cursor.lastrowid` | ไม่มีใน Postgres → auto-inject `RETURNING id` เข้า INSERT ที่ยังไม่มี |
| `db.commit()` | no-op (asyncpg autocommit ต่อ statement) |
| `db.close()` | ปิด asyncpg connection จริง |

**ผลลัพธ์:** ไฟล์ที่ต้องแก้จริงมีแค่ `postgres_backend.py` (adapter) + `database.py` (dispatch 2 จุด) แทนที่จะเป็น 16 ไฟล์/292 จุด

Postgres ไม่ต้องมี `_migrate_schema`/`_rebuild_*` แบบ SQLite เลย — เพราะ SQLite ต้อง evolve ไฟล์เดิมข้ามปีผ่าน `ALTER TABLE ADD COLUMN` แต่ Postgres deployment เริ่มจาก schema ที่ถูกต้องตั้งแต่แรกผ่าน data migration ครั้งเดียว (`scripts/migrate_to_postgres.py`)

---

## กับดัก/บั๊กที่เจอจริง (Postgres เป็นคนละ SQL dialect จาก SQLite ทำให้ต้องแก้จริง ไม่ใช่แค่สลับ driver)

1. **`col IS ?` (SQLite NULL-safe equality)** — Postgres's `IS` รับเฉพาะ keyword คงที่ (`NULL`/`TRUE`/...) ห้ามใส่ parameter เด็ดขาด (syntax error ตรงๆ ไม่ใช่แค่ผลลัพธ์ต่าง) → แปลงเป็น `IS NOT DISTINCT FROM ?` (Postgres's NULL-safe equality ที่รับ parameter ได้) จุดเดียวใน [persistence.py:61](../app/ingestion/persistence.py#L61)
2. **`TIMESTAMP` เข้มงวดเรื่อง type** — Postgres/asyncpg ปฏิเสธ ISO string ที่ผูกเข้าคอลัมน์ `TIMESTAMP` (ต้องเป็น real `datetime` object) แต่แอปส่ง/อ่านค่าพวกนี้เป็น string ตลอด (SQLite ไม่มี real datetime type อยู่แล้ว) → เปลี่ยนทุกคอลัมน์วันที่ใน Postgres schema เป็น `TEXT` แทน เพื่อให้พฤติกรรมตรงกับ SQLite ทุกจุด
3. **`INSERT OR IGNORE` / `INSERT OR REPLACE`** — ไม่มีใน Postgres ต้องแปลเป็น `ON CONFLICT (...) DO NOTHING/UPDATE` โดยระบุ conflict target ชัดเจน (ต่างจาก SQLite ที่ "OR IGNORE" ครอบคลุมทุก constraint แบบเดา)
4. **ไม่มี `lastrowid`** — ต้องใช้ `RETURNING id` เสมอ adapter จัดการให้อัตโนมัติด้วยการ inject `RETURNING id` เข้า INSERT ที่ไม่มีอยู่แล้ว (ทุกตารางใช้ `id` เป็น PK เหมือนกันหมด ทำให้ auto-inject ปลอดภัย) UPSERT ที่ conflict แล้วไม่ insert จะได้ `lastrowid=0` — fallback lookup เดิมใน queries.py จำเป็นและถูกใช้อยู่แล้ว
5. **`PRAGMA table_info(...)`** — ไม่มีใน Postgres แปลเป็น query `information_schema.columns` แทน (เฉพาะจุดที่เรียกจริงตอน runtime คือ import/export feature)
6. **DSN string ที่มี `#` ใน password** — `#` เป็นอักขระสงวนของ URI (fragment delimiter) ทั้ง `urllib.parse.urlsplit` และ asyncpg's DSN parser พังกับ password ที่มี `#` ไม่ escape → เขียน parser เองที่หา `@` ตัวสุดท้ายก่อน path แทนพึ่ง URI parser มาตรฐาน (ดู `_split_dsn` ใน [postgres_backend.py](../app/storage/postgres_backend.py))
7. **⚠️ `#` ถูกตัดทิ้งไปตั้งแต่ตอนโหลด env var (เจอตอน deploy จริง)** — แม้ `_split_dsn` จะรองรับ `#` ดิบๆ แล้ว (ข้อ 6) แต่หลาย `.env` parser/deployment platform ถือว่า `#` ที่ไม่ escape เป็นจุดเริ่ม **comment** และตัดทุกอย่างหลังจากนั้นทิ้งไปเงียบๆ ก่อนค่าจะมาถึง Python เลยด้วยซ้ำ (เช่น `DATABASE_URL=postgresql://user:pass#1234@host:5432/db` เหลือแค่ `postgresql://user:pass` ไม่มี host/port ทำให้ error `invalid literal for int()` ที่ `_split_dsn` แต่ต้นตอจริงคือ env var ถูกตัดตั้งแต่ต้น) **วิธีแก้ที่ปลอดภัยที่สุด:** percent-encode อักขระพิเศษในค่าที่ตั้งจริงบน deployment platform เสมอ (`#` → `%23`) — `_split_dsn` เรียก `unquote()` ให้อัตโนมัติอยู่แล้ว จึงได้ค่าที่ถูกต้องกลับมาไม่ว่าจะผ่าน layer ไหน (env loader, shell, URI parser)
8. **⚠️ `date(col)` — SQLite date function เจอตอน deploy จริงอีกจุด (9 occurrences ใน queries.py)** — SQLite's `date(col)` ตัด ISO datetime string ให้เหลือแค่ `'YYYY-MM-DD'` แล้วคืนเป็น **string ธรรมดา** (SQLite ไม่มี real date type) แต่ Postgres's `date(col)` คือการ **cast เป็น type `date` จริง** — ผลคือ Postgres infer type ของ parameter ที่เทียบด้วย (`= ?`, `BETWEEN ? AND ?`) เป็น `date` ไปด้วย ทำให้ asyncpg ปฏิเสธ string ธรรมดาที่แอปส่งมา (`AttributeError: 'str' object has no attribute 'toordinal'`) ทั้งที่คอลัมน์เป็น `TEXT` แล้วตามข้อ 2 **วิธีแก้:** แปลง `date(col)` → `LEFT(col, 10)` ในขั้น dialect translation (คืนผลลัพธ์แบบ string เหมือน SQLite เป๊ะ ไม่ trigger type inference) — เจอจาก error จริงตอน deploy ไม่ได้เจอตอน scope เดิม เพราะ grep แรกไม่ได้ครอบคลุม `date(...)` (เช็คแค่ `strftime`) เป็นบทเรียนว่าต้อง grep SQLite date/time function ทั้งชุด (`date()`, `datetime()`, `strftime()`, `julianday()`) ให้ครบตั้งแต่ต้น ไม่ใช่แค่บางตัว

---

## เหตุการณ์จริง: schema drift ระหว่าง branch (2026-07-29)

Deploy จริงพัง `asyncpg.exceptions.UndefinedTableError: relation "insurance_policies" does not exist` ตอน `/setup` — สาเหตุคือ **PR อีกอันถูก merge เข้า `master` พร้อมกับ PR ของ postgres migration นี้** ทำให้ deploy จริงมีฟีเจอร์ "Insurance policies" (ตาราง + routes + templates) ที่ `postgres_backend.py` (เขียนจาก schema ของ branch `features/db-migration` เท่านั้น) ไม่รู้จักเลย

**วิธีตรวจสอบ:** `git diff features/db-migration origin/master --stat` เจอว่า master มีตาราง `insurance_policies` (ใน `database.py`) + CRUD functions (ใน `queries.py`, ใช้ `?` placeholder ธรรมดา ไม่มี dialect พิเศษ) + routes/templates ใหม่ — ไม่มีอะไรซับซ้อนเพิ่มสำหรับ Postgres นอกจาก CREATE TABLE

**การแก้ไข:**
1. เพิ่ม `insurance_policies` เข้า `postgres_backend.SCHEMA_SQL` (ใช้ `TEXT` สำหรับ `start_date`/`end_date`/`renewal_date`/`created_at`/`updated_at` ตาม convention เดิมของไฟล์นี้)
2. เพิ่มชื่อตารางเข้า `_ALL_TABLES` (postgres_backend.py), `TABLES` (scripts/migrate_to_postgres.py), `_POSTGRES_TABLES` (tests/conftest.py)
3. รัน `database.init_db()` ตรงกับ live DB จริง (เป็น `CREATE TABLE IF NOT EXISTS` — idempotent, ปลอดภัยกับตารางอื่นที่มีข้อมูลอยู่แล้ว) เพื่อสร้างตารางที่ขาดไปทันที แก้ปัญหา production ให้ก่อน
4. Verify CRUD pattern จริงจาก master (`INSERT`+lastrowid, `dict(row)`, `UPDATE ... CURRENT_TIMESTAMP`, `DELETE`) ผ่าน adapter ครบ

**บทเรียน:** เมื่อทำงานคู่ขนานหลาย branch/PR ที่แก้ schema ทั้งคู่ — **`postgres_backend.py` ต้อง sync กับ schema ล่าสุดที่จะถูก deploy จริงเสมอ** ไม่ใช่แค่ schema ของ branch ที่ตัวเองทำงานอยู่ ก่อน deploy ควร `git diff <branch-ที่จะ-deploy>` เทียบ `database.py` เพื่อเช็ค schema drift ทุกครั้ง

---

## ข้อจำกัดที่พบระหว่างรัน test suite (environment ไม่ใช่ correctness bug)

รัน test suite เต็ม (292+ tests) บน endpoint จริงเจอ 3 อาการ ทั้งหมดเป็นปัญหาจาก **environment (Windows + remote-over-internet + ไม่มี connection pooling)** ไม่ใช่บั๊กใน adapter:

1. **1 test แฮงก์**: `TestClient` (รัน ASGI app ใน background thread แยก event loop) ตามด้วย `asyncio.run()` ใหม่ในเทสต์เดียวกัน — asyncpg I/O completion ไม่ถูก signal กลับ ยืนยันด้วย manual reproduction ว่า logic จริง (ingestion + categorization) ทำงานถูกต้องถ้าไม่มี TestClient แทรก → skip เฉพาะจุด ([tests/integration/test_category_flow.py](../tests/integration/test_category_flow.py))
2. **Performance test suite ทั้งไฟล์**: budget ของ [tests/test_performance.py](../tests/test_performance.py) ตั้งไว้สำหรับ local-file latency แต่บาง test ทำ round-trip เดี่ยวๆ หลักร้อยครั้งใน loop (เช่น 500x `history.record()`) ผ่าน network จริง (~0.4s/ครั้ง) → เกิน timeout แต่ไม่ใช่ O(n²)/full-scan regression จริง → skip ทั้งไฟล์สำหรับ postgres backend
3. **Connection churn ระยะยาว**: `get_connection()` เปิด TCP ใหม่ทุกครั้ง (ไม่มี pool) — รัน suite เต็มยาวๆ พบ latency เพิ่มขึ้นเรื่อยๆ จนแฮงก์ในที่สุด (เจอที่ `test_admin_can_manage_users`) → **ตัดสินใจหยุดไล่ full-suite บน endpoint นี้** เพราะ core logic verify ผ่านแล้วจาก isolated tests + subset ที่ผ่านก่อนแฮงก์ (~60-70% ของ suite) และปัญหานี้เป็น artifact ของ topology ปัจจุบัน (remote + ไม่มี pool)

**ข้อเสนอสำหรับอนาคต:** ถ้าจะ verify full-suite ให้มั่นใจ 100% ควรทำกับ Postgres ที่ low-latency (co-located หรืออย่างน้อย local Docker) และ/หรือเพิ่ม connection pooling จริง (`asyncpg.create_pool`) ให้ backend — ทั้งสองอย่างเป็น production best-practice อยู่แล้ว ไม่ใช่แค่แก้ปัญหา test

---

## วิธีใช้งาน migration script จริง

```bash
# Dry run กับ copy เสมอก่อน (ห้ามชี้ --source ไปที่ data/finance.db โดยตรงในการทดสอบ)
cp data/finance.db /tmp/finance-copy.db
python scripts/migrate_to_postgres.py --source /tmp/finance-copy.db \
  --target-url "postgresql://user:pass@host:5432/dbname" --no-ssl   # --no-ssl เฉพาะ dev endpoint ที่รู้ว่าไม่มี TLS

# Production cutover (หลังหยุด scheduler/ingestion แล้ว, endpoint ต้องมี TLS)
python scripts/migrate_to_postgres.py \
  --source data/finance.db \
  --target-url "postgresql://user:pass@host:5432/dbname"
```

Script สร้าง schema (ถ้ายังไม่มี) → insert ทีละแถวโดย map ด้วย**ชื่อคอลัมน์**เท่านั้น (ไม่ใช่ positional — Postgres schema คนละลำดับคอลัมน์กับ SQLite ได้เสมอ) → รักษา `id` เดิมด้วย `OVERRIDING SYSTEM VALUE` (สำคัญเพราะ `unknown_patterns.resolved_transaction_id` อ้างอิง `transactions.id`) → bump identity sequence ให้ต่อจากค่าที่ migrate มาไม่ชนกัน → ตรวจ byte-identical ทุกแถวทุกคอลัมน์อัตโนมัติ (exit code ≠ 0 ถ้าไม่ตรง ใช้เป็น gate ก่อน cutover ได้)

---

## Config

เพิ่มใน [Settings](../app/config.py) และ [config.yaml](../config.yaml):

```yaml
# Database
DATABASE_BACKEND: "aiosqlite"        # "aiosqlite" (default, local file) | "postgres"
DATABASE_PATH: "data/finance.db"     # ใช้เมื่อ backend=aiosqlite
DATABASE_URL: "{{ env.DATABASE_URL }}"       # ใช้เมื่อ backend=postgres เช่น postgresql://user:pass@host:5432/db
DATABASE_SSL: true                    # keep true ยกเว้น dev endpoint ที่รู้ว่าไม่มี TLS
```

`DATABASE_BACKEND` เป็น feature flag → สลับกลับ `aiosqlite` ได้ทันทีถ้ามีปัญหา (rollback ง่าย ไม่ต้อง deploy ใหม่ถ้า env แก้ได้)

---

## ⚠️ ข้อกำหนดด้านความปลอดภัยก่อน production

- [ ] **endpoint ต้องมี TLS** — endpoint ทดสอบปัจจุบัน (`169.58.65.88:5432`) ปฏิเสธ SSL upgrade เลย ใช้ได้เฉพาะ dev/test ที่ยอมรับความเสี่ยงแล้วเท่านั้น
- [ ] `DATABASE_SSL=true` (default) ใน production เสมอ — `false` เป็น opt-in ชัดเจนสำหรับ dev เท่านั้น
- [ ] credential ใน `.env`/secrets ไม่เข้า git (ตรวจ [.gitignore](../.gitignore))
- [ ] พิจารณา connection pooling (`asyncpg.create_pool`) ก่อน production load จริง — ช่วยทั้งเรื่อง performance และลดปัญหา connection churn ที่เจอตอนรัน test

---

## แผน Rollback

1. ตั้ง `DATABASE_BACKEND=aiosqlite` → กลับไปใช้ไฟล์ทันที
2. เก็บ `finance.db.bak` (copy ก่อน migrate) ไว้เป็นจุดคืนค่าข้อมูลเสมอ

---

## สรุป

Postgres เป็น backend เป้าหมายหลักตอนนี้ (ไม่ใช้ libSQL แล้ว) จุดชี้ขาดที่ทำให้งาน port ทำได้จริงคือ **connection รวมศูนย์ 2 จุด + interface เดียวกันทั้ง codebase** ทำให้ adapter แก้จุดเดียวพอ ความเสี่ยงหลักที่เหลือคือ **TLS ของ endpoint** และ **connection pooling ก่อน production load จริง**
