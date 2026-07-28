# LLM 1B Extraction Guide

Purpose: give a small model enough rigid structure to classify bank email templates and extract transaction fields without creative inference.

Use this guide with `parser-template-catalog.md`.

## Highest Priority Rules

1. Subject decides the template first.
2. Sender decides the bank second.
3. Extract only from known labels and nearby values.
4. Never invent `amount`, `occurred_at`, `transaction_id`, or `counterparty`.
5. If subject is ignored, return `should_import=false` immediately and stop.
6. If a value is missing, return `null` plus a warning. Do not guess from footer text.
7. Convert Buddhist year to Gregorian: if year > 2400, subtract 543.
8. Parse numeric amount by removing commas and currency text only.

## Fixed Output JSON

Return exactly one JSON object:

```json
{
  "should_import": true,
  "bank": null,
  "template_id": null,
  "transaction_type": "unknown",
  "direction": "unknown",
  "status": "unknown",
  "occurred_at": null,
  "amount": null,
  "fee": 0.0,
  "available_balance": null,
  "counterparty": null,
  "from_account": null,
  "to_account": null,
  "description": null,
  "transaction_id": null,
  "raw_fields": {},
  "warnings": []
}
```

Allowed enum values:

- `transaction_type`: `bill_payment`, `bank_transfer`, `promptpay_transfer`, `topup`, `notification`, `unknown`
- `direction`: `in`, `out`, `unknown`
- `status`: `success`, `failed`, `pending`, `cancelled`, `ignored`, `unknown`

## Rule Order

### Step 1: Bank

- Sender contains `KPLUS` or `kasikornbank`: `bank=kbank`
- Sender contains `krungsri` or `admin@krungsri.com`: `bank=krungsri`
- Sender contains `LHBYou` or `lhbank`: `bank=lhbank`
- Sender contains `scbeasynet` or `SCB Easy`: `bank=scb`

### Step 2: Ignore

Return this object when ignored:

```json
{
  "should_import": false,
  "transaction_type": "notification",
  "direction": "unknown",
  "status": "ignored",
  "warnings": []
}
```

Ignored subjects:

- KBank: subject contains `email statement`
- LHBank: subject contains `Login Notification`
- LHBank: subject contains `การเข้าใช้งานแอปพลิเคชัน`

### Step 3: Template ID

Use exact or contains matching:

- `kbank_bill_payment_success`: KBank + `Result of Bill Payment (Success)`
- `kbank_funds_transfer_success`: KBank + `Result of Funds Transfer (Success)`
- `kbank_promptpay_transfer_success`: KBank + `Result of PromptPay Funds Transfer (Success)`
- `krungsri_bill_payment_success`: Krungsri + `Result of bill payment (Success)`
- `krungsri_promptpay_transfer_success`: Krungsri + `Result of fund transfer to PromptPay (Success)`
- `krungsri_other_account_transfer_success`: Krungsri + `Result of fund transfer to other person's account (Success)`
- `krungsri_ewallet_topup_success`: Krungsri + `transfer-ewallet-result-success`
- `lhbank_bill_payment_success`: LHBank + `จ่ายบิลสำเร็จ` or `Successful Bill Payment`
- `lhbank_transfer_success`: LHBank + `โอนเงินสำเร็จ` or `Successful Transfer`
- `scb_transaction_notification`: SCB transaction notification subject

## Template Constants

Set these before field extraction:

| Template | Type | Direction | Status |
| --- | --- | --- | --- |
| `kbank_bill_payment_success` | `bill_payment` | `out` | `success` |
| `kbank_funds_transfer_success` | `bank_transfer` | `out` | `success` |
| `kbank_promptpay_transfer_success` | `promptpay_transfer` | `out` | `success` |
| `krungsri_bill_payment_success` | `bill_payment` | `out` | `success` |
| `krungsri_promptpay_transfer_success` | `promptpay_transfer` | `out` | `success` |
| `krungsri_other_account_transfer_success` | `bank_transfer` | `out` | `success` |
| `krungsri_ewallet_topup_success` | `topup` | `out` | `success` |
| `lhbank_bill_payment_success` | `bill_payment` | `out` | `success` |
| `lhbank_transfer_success` | `bank_transfer` | `out` | `success` |
| `scb_transaction_notification` | `bank_transfer` | `out` | `success` |

## Label Maps

### KBank

Use label:value pairs. Values usually follow the label on the next line or same line.

Map labels:

- Date: `Transaction Date`, `วันที่ทำรายการ`
- Reference: `Transaction Number`, `เลขที่รายการ`
- From: `From Account`, `Paid From Account`, `ชำระเงินจากบัญชี`
- To: `To Account`, `To PromptPay ID`
- Counterparty: `Account Name`, `Received Name`, `Company Name`, `เพื่อเข้าบัญชีบริษัท`
- Amount: `Amount (THB)`, `จำนวนเงิน (บาท)`
- Fee: `Fee (THB)`, `ค่าธรรมเนียม (บาท)`
- Balance: `Available Balance (THB)`, `ยอดถอนได้ (บาท)`, `ยอดเงินคงเหลือ`

Template-specific:

- Funds transfer counterparty = `Account Name`
- PromptPay counterparty = `Received Name`
- Bill payment counterparty = `Company Name` or Thai company/biller label

### Krungsri

Most labels end with `:`, and value is next non-empty line(s).

Before extraction, merge split currency labels:

- `จำนวนเงิน` + `(บาท):` = `จำนวนเงิน (บาท):`
- `ค่าธรรมเนียม` + `(บาท):` = `ค่าธรรมเนียม (บาท):`

Map labels:

- Result: `ผลการทำรายการ`
- Type label: `ประเภทรายการ`
- From: `หักจากบัญชี`, `จากบัญชี`, `บัญชีผู้โอน`
- Counterparty: `ผู้รับชำระเงิน`, `ไปยังพร้อมเพย์`, `บัญชีผู้รับโอน`
- Wallet: `e-Wallet`
- Amount: `จำนวนเงิน (บาท)`
- Fee: `ค่าธรรมเนียม (บาท)`
- Reference: `หมายเลขอ้างอิง`
- Date/time: `วัน-เวลาที่ทำรายการ`
- Memo: `บันทึกช่วยจำ`
- Extra references: `รหัสร้านค้า`, `รหัสธุรกรรม`, `รหัสอ้างอิง 1`, `รหัสอ้างอิง 2`, `หมายเลขร้านค้า1`, `เลขที่อ้างอิง2`

Footer guard:

- If memo starts with `หากท่านไม่ได้เป็นผู้ทำรายการ`, set memo to empty string.

### LHBank

Known labels:

- Date/time: `วันเวลา`
- Device: `อุปกรณ์`
- From: `จาก`
- Counterparty/to: `ไปยัง`
- Merchant code: `หมายเลขร้านค้า1`
- Reference: `เลขที่อ้างอิง2`
- Amount: `จำนวนเงิน (บาท)`
- Fee: `ค่าธรรมเนียม (บาท)`
- Memo: `บันทึกช่วยจำ`

Extraction rule:

- A label collects following non-empty lines until the next known label.
- `จาก` and `ไปยัง` values may span multiple lines.

PromptPay value rule:

- If `ไปยัง` contains `หมายเลขพร้อมเพย์` and a following `id : name`, keep full raw value.
- Prefer the name portion as `counterparty` only if confidently separable.

### SCB

Support both section and inline forms.

Labels:

- Type: `ประเภทของรายการ`
- Details: `รายละเอียด`
- Amount: `จำนวนเงิน`
- Date/time: `วันและเวลาการทำรายการ`

Details parsing:

- From pattern: `จาก ธนาคาร... เบอร์บัญชี ...`
- To pattern: `ไปยัง ธนาคาร... เบอร์บัญชี ...`
- Extract `from_bank`, `from_account`, `to_bank`, `to_account`.

Inline examples:

- `ประเภทของรายการ:โอนเงินไปธนาคารอื่น`
- `รายละเอียด:จาก ธนาคารไทยพาณิชย์ เบอร์บัญชี xxxxxx`
- `ไปยัง ธนาคารKBank เบอร์บัญชี xxxxxx`
- `จำนวนเงิน 10.00 บาท`
- `วันและเวลาการทำรายการ:28 ก.ค. 2569 ณ 07:16:35`

## Validation

Complete transaction requires:

- `amount`
- `occurred_at`
- a known template/type

Counterparty is strongly preferred:

- If missing but amount/date/type exist, return warning `Missing counterparty`.

Hard fail:

- Importable template with no amount.
- Importable template with no occurred_at.

Ignored:

- Do not store as transaction.
- Do not store as unknown pattern.

## Tiny Model Reminders

- Do not summarize.
- Do not explain.
- Return JSON only.
- Prefer exact label matches over semantic guesses.
- Use subject constants for type/direction/status.
- Do not parse footer/legal/security text as a transaction field.
- When duplicate bilingual fields exist, keep the first complete value.
