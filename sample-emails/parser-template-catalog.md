# Parser Template Catalog

Source set: `sample-emails/index.json`, 120 bank emails from the last 20 days.

Analysis output: `sample-emails/parser-analysis-raw.json`

Coverage after parser updates:

| Bank | Template | Count | Import | Type | Direction | Parse status |
| --- | --- | ---: | --- | --- | --- | --- |
| KBank | `Result of Bill Payment (Success)` | 22 | yes | `bill_payment` | `out` | 22 complete |
| KBank | `Result of Funds Transfer (Success)` | 10 | yes | `bank_transfer` | `out` | 10 complete |
| KBank | `Result of PromptPay Funds Transfer (Success)` | 11 | yes | `promptpay_transfer` | `out` | 11 complete |
| KBank | `K PLUS : Your requested K-Credit Card email statement ...` | 6 | no | `notification` | `unknown` | 6 ignored |
| Krungsri | `Result of bill payment (Success)` | 18 | yes | `bill_payment` | `out` | 18 complete |
| Krungsri | `Result of fund transfer to PromptPay (Success)` | 8 | yes | `promptpay_transfer` | `out` | 8 complete |
| Krungsri | `Result of fund transfer to other person's account (Success)` | 1 | yes | `bank_transfer` | `out` | 1 complete |
| Krungsri | `transfer-ewallet-result-success` | 6 | yes | `topup` | `out` | 6 complete |
| LHBank | `[แจ้งเตือน] - การเข้าใช้งานแอปพลิเคชัน / Login Notification.` | 18 | no | `notification` | `unknown` | 18 ignored |
| LHBank | `[แจ้งเตือน] - จ่ายบิลสำเร็จ / Successful Bill Payment` | 8 | yes | `bill_payment` | `out` | 8 complete |
| LHBank | `[แจ้งเตือน] - โอนเงินสำเร็จ / Successful Transfer` | 11 | yes | `bank_transfer` | `out` | 11 complete |
| SCB | `แจ้งเตือนจากแอพ SCB Easy: บริการอัตโนมัติแจ้งเตือนการทำธุรกรรม` | 1 | yes | `bank_transfer` | `out` | 1 complete |

## Common Canonical Output

All parsers should produce this shape:

```json
{
  "should_import": true,
  "bank": "kbank|krungsri|lhbank|scb",
  "template_id": "string",
  "transaction_type": "bill_payment|bank_transfer|promptpay_transfer|topup|notification|unknown",
  "direction": "in|out|unknown",
  "status": "success|failed|pending|cancelled|ignored|unknown",
  "occurred_at": "YYYY-MM-DDTHH:MM:SS",
  "amount": 0.0,
  "fee": 0.0,
  "available_balance": null,
  "counterparty": "string|null",
  "description": "string|null",
  "transaction_id": "string|null",
  "raw_fields": {}
}
```

Date rules:

- Thai Buddhist year: subtract 543 when year is greater than 2400.
- KBank English dates are day-first.
- Krungsri format: `DD/MM/YYYY HH:MM:SS`.
- LHBank format: `วัน..., DD <Thai month> YYYY HH:MM`.
- SCB format: `DD <Thai month> YYYY ณ HH:MM:SS`.

Amount rules:

- Remove commas and currency text: `บาท`, `THB`, `Baht`.
- Parse `0.00` as valid, not missing.
- Do not infer an amount from unrelated footer/legal text.

Import rules:

- `should_import=false` for KBank credit card statement emails.
- `should_import=false` for LHBank subjects containing `Login Notification` or `การเข้าใช้งานแอปพลิเคชัน`.
- Unknown parser failures belong in `unknown_patterns`; ignored notifications do not.

## KBank Templates

Sender keys:

- `K PLUS <KPLUS@kasikornbank.com>`
- Match sender containing `kasikornbank` or `kplus`.

### KBank Bill Payment Success

Subject:

- `Result of Bill Payment (Success)`

Canonical:

- `transaction_type=bill_payment`
- `direction=out`
- `status=success`
- `transaction_id`: `Transaction Number` or `เลขที่รายการ`
- `counterparty`: `Company Name`, `เพื่อเข้าบัญชีบริษัท`, `ผู้ให้บริการ`, or merchant/biller label
- `from_account`: `Paid From Account` or `ชำระเงินจากบัญชี`
- `amount`: `Amount (THB)` or `จำนวนเงิน (บาท)`
- `fee`: `Fee (THB)` or `ค่าธรรมเนียม (บาท)`
- `available_balance`: `Available Balance (THB)`, `ยอดถอนได้ (บาท)`, or `ยอดเงินคงเหลือ`

Observed label sequences:

- Thai-dominant: `วันที่ทำรายการ`, `เลขที่รายการ`, `ชำระเงินจากบัญชี`, `เพื่อเข้าบัญชีบริษัท`, optional merchant/reference label, `จำนวนเงิน (บาท)`, `ค่าธรรมเนียม (บาท)`, `ยอดถอนได้ (บาท)`
- English-dominant: `Subject`, `Transaction Date`, `Transaction Number`, `Paid From Account`, `Company Name`, optional `REFERENCE 1` or `Card No.`, `Amount (THB)`, `Fee (THB)`, `Available Balance (THB)`

Example files:

- `sample-emails/002_20260727-162529_kbank_Result-of-Bill-Payment-Success_19fa2e4e1a78ff4f.txt`
- `sample-emails/007_20260727-070549_kbank_Result-of-Bill-Payment-Success_19fa0e477622cf3f.txt`

### KBank Funds Transfer Success

Subject:

- `Result of Funds Transfer (Success)`

Canonical:

- `transaction_type=bank_transfer`
- `direction=out`
- `status=success`
- `transaction_id`: `Transaction Number`
- `from_account`: `From Account`
- `to_account`: `To Account`
- `counterparty`: `Account Name`
- Optional counterparty detail: combine `To Bank` + `To Account` + `Account Name` for display/search.
- `amount`: `Amount (THB)`
- `fee`: `Fee (THB)`
- `available_balance`: `Available Balance (THB)`

Observed label sequence:

- `Subject`, `Transaction Date`, `Transaction Number`, `From Account`, `To Bank`, `To Account`, `Account Name`, `Amount (THB)`, `Fee (THB)`, `Available Balance (THB)`

Example file:

- `sample-emails/005_20260727-125637_kbank_Result-of-Funds-Transfer-Success_19fa2258fe1b07c0.txt`

### KBank PromptPay Funds Transfer Success

Subject:

- `Result of PromptPay Funds Transfer (Success)`

Canonical:

- `transaction_type=promptpay_transfer`
- `direction=out`
- `status=success`
- `transaction_id`: `Transaction Number`
- `from_account`: `From Account`
- `to_account`: `To PromptPay ID`
- `counterparty`: `Received Name`
- `amount`: `Amount (THB)`
- `fee`: `Fee (THB)`
- `available_balance`: `Available Balance (THB)`

Observed label sequence:

- `Subject`, `Transaction Date`, `Transaction Number`, `From Account`, `To PromptPay ID`, `Received Name`, `Amount (THB)`, `Fee (THB)`, `Available Balance (THB)`

Example file:

- `sample-emails/009_20260726-183309_kbank_Result-of-PromptPay-Funds-Transfer-Success_19f9e336943dffe3.txt`

### KBank Credit Card Email Statement

Subject pattern:

- `K PLUS : Your requested K-Credit Card email statement MM/YYYY [id]`

Canonical:

- `should_import=false`
- `parse_status=ignored`
- `status=ignored`
- `ignored_reason=non_transaction_notification`

Reason:

- This is a statement delivery notification, not a transaction notification.

Example file:

- `sample-emails/091_20260713-205645_kbank_K-PLUS-Your-requested-K-Credit-Card-email-statement-06-2026-49900845_19f5bc4416a13e4d.txt`

## Krungsri Templates

Sender:

- `krungsri app <admin@krungsri.com>`

Important layout rule:

- Some amount labels are split across two physical lines:
  - `จำนวนเงิน` then `(บาท):`
  - `ค่าธรรมเนียม` then `(บาท):`
- Merge them into `จำนวนเงิน (บาท):` and `ค่าธรรมเนียม (บาท):` before extracting.

Footer rule:

- Do not treat security/footer text after an empty `บันทึกช่วยจำ:` as memo content.
- If memo value starts with bank security wording such as `หากท่านไม่ได้เป็นผู้ทำรายการ`, treat memo as blank.

### Krungsri Bill Payment Success

Subject:

- `Result of bill payment (Success)`

Canonical:

- `transaction_type=bill_payment`
- `direction=out`
- `status=success`
- `transaction_id`: `หมายเลขอ้างอิง`
- `from_account`: `หักจากบัญชี`
- `counterparty`: `ผู้รับชำระเงิน`
- `amount`: `จำนวนเงิน (บาท)`
- `fee`: `ค่าธรรมเนียม (บาท)`
- Additional references: `รหัสร้านค้า`, `รหัสธุรกรรม`, `รหัสอ้างอิง 1`, `รหัสอ้างอิง 2`, `หมายเลขร้านค้า1`, `เลขที่อ้างอิง2`, `เลขที่อ้างอิง 1`, `เลขที่อ้างอิง 2`

Observed label families:

- `ผลการทำรายการ`, `ประเภทรายการ`, `หักจากบัญชี`, `ผู้รับชำระเงิน`, `จำนวนเงิน (บาท)`, `ค่าธรรมเนียม (บาท)`, reference labels, `หมายเลขอ้างอิง`, `วัน-เวลาที่ทำรายการ`, `บันทึกช่วยจำ`

Example file:

- `sample-emails/074_20260717-124903_krungsri_Result-of-bill-payment-Success_19f6e9f1b054e12c.txt`

### Krungsri PromptPay Transfer Success

Subject:

- `Result of fund transfer to PromptPay (Success)`

Canonical:

- `transaction_type=promptpay_transfer`
- `direction=out`
- `status=success`
- `transaction_id`: `หมายเลขอ้างอิง`
- `from_account`: `จากบัญชี`
- `counterparty`: `ไปยังพร้อมเพย์`
- `amount`: `จำนวนเงิน (บาท)`
- `fee`: `ค่าธรรมเนียม (บาท)`

Observed labels:

- `ผลการทำรายการ`, `ประเภทรายการ`, `จากบัญชี`, `ไปยังพร้อมเพย์`, `จำนวนเงิน (บาท)`, `ค่าธรรมเนียม (บาท)`, `หมายเลขอ้างอิง`, `วัน-เวลาที่ทำรายการ`, `บันทึกช่วยจำ`

Example file:

- `sample-emails/070_20260718-122319_krungsri_Result-of-fund-transfer-to-PromptPay-Success_19f73adea9602568.txt`

### Krungsri Other Account Transfer Success

Subject:

- `Result of fund transfer to other person's account (Success)`

Canonical:

- `transaction_type=bank_transfer`
- `direction=out`
- `status=success`
- `transaction_id`: `หมายเลขอ้างอิง`
- `from_account`: `บัญชีผู้โอน`
- `counterparty`: `บัญชีผู้รับโอน`
- Optional detail: `ธนาคารของบัญชีผู้รับโอน`
- `amount`: `จำนวนเงิน (บาท)`
- `fee`: `ค่าธรรมเนียม (บาท)`

Example file:

- `sample-emails/080_20260715-193804_krungsri_Result-of-fund-transfer-to-other-person-s-account-Success_19f65c8e6b1f6e2e.txt`

### Krungsri E-Wallet Topup Success

Subject:

- `transfer-ewallet-result-success`

Canonical:

- `transaction_type=topup`
- `direction=out`
- `status=success`
- `transaction_id`: `หมายเลขอ้างอิง`
- `from_account`: `หักจากบัญชี`
- `counterparty`: `ผู้รับชำระเงิน`
- `to_wallet`: `e-Wallet`
- `amount`: `จำนวนเงิน (บาท)`
- `fee`: `ค่าธรรมเนียม (บาท)`

Observed labels:

- `ผลการทำรายการ`, `ประเภทรายการ`, `หักจากบัญชี`, `ผู้รับชำระเงิน`, `ไปยัง`, `e-Wallet`, `จำนวนเงิน (บาท)`, `ค่าธรรมเนียม (บาท)`, `หมายเลขอ้างอิง`, `วัน-เวลาที่ทำรายการ`, `บันทึกช่วยจำ`

Example file:

- `sample-emails/081_20260715-171809_krungsri_transfer-ewallet-result-success_19f6548c621d598c.txt`

## LHBank Templates

Sender:

- `LHB You <LHBYou@lhbank.co.th>`

Layout rule:

- Label is often on one line and value is on the following line(s).
- `จาก` and `ไปยัง` are both section headers and value-owning labels.
- A value may span multiple lines until the next known label.

### LHBank Login Notification

Subject:

- `[แจ้งเตือน] - การเข้าใช้งานแอปพลิเคชัน / Login Notification.`

Canonical:

- `should_import=false`
- `parse_status=ignored`
- `status=ignored`
- `ignored_reason=non_transaction_notification`

Observed labels:

- `วันเวลา`, `อุปกรณ์`

Example file:

- `sample-emails/003_20260727-150441_lhbank_Login-Notification_19fa29ad5990f8c2.txt`

### LHBank Successful Bill Payment

Subject:

- `[แจ้งเตือน] - จ่ายบิลสำเร็จ / Successful Bill Payment`

Canonical:

- `transaction_type=bill_payment`
- `direction=out`
- `status=success`
- `from_account`: `จาก`
- `counterparty`: `ไปยัง`
- `amount`: `จำนวนเงิน (บาท)`
- `fee`: `ค่าธรรมเนียม (บาท)`
- `transaction_id`: `เลขที่อ้างอิง2`, when present
- Extra reference: `หมายเลขร้านค้า1`

Observed labels:

- Common: `วันเวลา`, `อุปกรณ์`, `จาก`, `ไปยัง`, `จำนวนเงิน (บาท)`, `ค่าธรรมเนียม (บาท)`, `บันทึกช่วยจำ`
- Optional references: `หมายเลขร้านค้า1`, `เลขที่อ้างอิง2`

Example file:

- `sample-emails/015_20260726-121537_lhbank_Successful-Bill-Payment_19f9cd9adbf470fb.txt`

### LHBank Successful Transfer

Subject:

- `[แจ้งเตือน] - โอนเงินสำเร็จ / Successful Transfer`

Canonical:

- `transaction_type=bank_transfer`
- `direction=out`
- `status=success`
- `from_account`: `จาก`
- `counterparty`: `ไปยัง`
- `amount`: `จำนวนเงิน (บาท)`
- `fee`: `ค่าธรรมเนียม (บาท)`

Observed labels:

- `วันเวลา`, `อุปกรณ์`, `จาก`, `ไปยัง`, `จำนวนเงิน (บาท)`, `ค่าธรรมเนียม (บาท)`, `บันทึกช่วยจำ`

Important value pattern:

- `ไปยัง` can contain a line such as `หมายเลขพร้อมเพย์` followed by `id : name`.
- Keep both lines in raw fields; use the name part as preferred display when possible.

Example file:

- `sample-emails/018_20260725-184559_lhbank_Successful-Transfer_19f9918b515ab7c2.txt`

## SCB Templates

Sender:

- `SCB Easy <scbeasynet@scb.co.th>`

### SCB Transaction Notification

Subject:

- `แจ้งเตือนจากแอพ SCB Easy: บริการอัตโนมัติแจ้งเตือนการทำธุรกรรม`

Canonical:

- `transaction_type=bank_transfer` when `ประเภทของรายการ` is `โอนเงินไปธนาคารอื่น` or contains `โอนเงิน`
- `direction=out`
- `status=success`
- `from_bank` and `from_account`: parsed from `รายละเอียด`
- `to_bank` and `to_account`: parsed from `รายละเอียด`
- `counterparty`: combine `to_bank` + `to_account`
- `amount`: `จำนวนเงิน`
- `occurred_at`: `วันและเวลาการทำรายการ`

Supported layouts:

- Section style:
  - `ประเภทของรายการ:` on one line, value on next line
  - `รายละเอียด:` then from/to lines
  - `จำนวนเงิน` then value
  - `วันและเวลาการทำรายการ:` then value
- Inline style:
  - `ประเภทของรายการ:โอนเงินไปธนาคารอื่น`
  - `รายละเอียด:จาก ... เบอร์บัญชี ...`
  - `ไปยัง ... เบอร์บัญชี ...`
  - `จำนวนเงิน 10.00 บาท`
  - `วันและเวลาการทำรายการ:28 ก.ค. 2569 ณ 07:16:35`

Example file:

- `sample-emails/001_20260728-071636_scb_SCB-Easy_19fa614a3107507b.txt`
