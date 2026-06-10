# Salesforce dedupe — validated query patterns

Org: `trycentssf` (`00D5f000008AE6LEAW`). Connector: SF MCP (`soql_query`, `find`,
`describe_sobject`). **Every call passes `x-gram-toolset-id` = `019e02db-e17c-7698-8a89-f00e3692570b`
unchanged.** All reads below were run live and confirmed working on 2026-06-08.

## Four objects — SF is the master record

A customer can appear as an **Account**, **Contact**, **Lead**, and/or **Custom_Location__c**
(Location). The MOOPS CUSTID is stamped on several of them — match on the id directly when present.

| Signal | Object.Field |
|--------|--------------|
| Contact email / phone / name | `Contact.Email`, `Contact.Phone`, `Contact.Name` / `LastName` |
| Lead email / phone / company | `Lead.Email`, `Lead.Phone`/`MobilePhone`, `Lead.Company`, `Lead.Name` |
| Business name / billing geo | `Account.Name`, `Account.BillingCity`, `Account.BillingState` |
| Location name / address / phone | `Custom_Location__c.Name`, `.Street__c`/`.City__c`/`.State_Province__c`/`.Zip_Postal_Code__c`, `.Phone_Number__c` |
| **CUSTID — customer** (bridge to Admin) | **`Account.LW_account_ID__c`**, **`Lead.Moops_Customer_id__c`** |
| **CUSTID — location** | **`Custom_Location__c.LW_Location_ID__c`**, `Lead.Moops_Location_Key_ID__c` |
| Location → parent account | `Custom_Location__c.Account__c` (= `Account_ID__c`) |
| Lead already converted? | `Lead.IsConverted` (true ⇒ truth is the Account/Contact it became) |
| More MOOPS context on Lead | `Moops_Sales_Order_Link__c`, `Moops_of_VACs__c`, `Moops_of_Cards__c`, `Existing_Customer__c`, `Moops_Distributor_who_placed_the_order__c`, `Moops_Location_Addresses__c` |

Notes:
- `Account` has **no** `Status` field (INVALID_FIELD in testing). Use `Type` (`Customer` /
  `Prospect` / `Distributor` / …).
- **CUSTID format varies**: MOOPS pads to 5 digits (`00378`); some SF records hold it unpadded
  (`1595`). Try both forms when matching by id.
- `Custom_Location__c.LW_Location_ID__c` (e.g. `8235`, `6902`) is a different numbering from
  `Lead.Moops_Location_Key_ID__c` (e.g. `0100001`, `6679`) — don't assume they're the same id;
  match each within its own field.

## 1. Email — exact, SOQL (STRONG)

```sql
SELECT Id, Name, Email, Phone, AccountId, Account.Name, Account.LW_account_ID__c,
       Account.BillingCity, Account.BillingState
FROM Contact WHERE Email = 'martinstorageunits@gmail.com'
```
Result for SOR-27723: `{"totalSize":0,"done":true,"records":[]}` → no match (genuinely new).

## 2. Phone — SOSL on digits (STRONG)

Phones are stored inconsistently (`+1 210-692-4735`, `(630) 892-2424`, `3172719828`, `+19092735609`),
so SOSL — which normalizes — beats exact SOQL.

```
FIND {9129771240} IN PHONE FIELDS
RETURNING Contact(Id, Name, Phone, AccountId, Account.Name, Account.LW_account_ID__c),
          Account(Id, Name, Phone, BillingCity, BillingState, LW_account_ID__c)
```
Result for SOR-27723: `{"searchRecords":[]}` → no match.

## 3. Business / location name — SOSL fuzzy (WEAK)

```
FIND {American Laundromat} IN NAME FIELDS
RETURNING Account(Id, Name, Phone, BillingCity, BillingState, LW_account_ID__c),
          Contact(Id, Name, Email, Account.Name)
```
Returns fuzzy hits — the exact account **plus** near-names. Confirm with city/state:
- `American Laundromat` — Jersey City, NJ — LW `00138` (Id `001S6000007pUPvIAM`) ← exact
- `The Great American Laundromat` — Clarksville, TN — LW `00675`
- `Pan American Laundromat`, `American Mega Laundromat`, `American California Laundromat` — near-misses

## 4. Last name — SOQL, only if 1–3 empty (WEAK)

```sql
SELECT Id, Name, Email, Phone, Account.Name, Account.LW_account_ID__c
FROM Contact WHERE LastName = 'Martin'
```

## 5. Domain sweep — useful catch-all (validated)

A SOSL on the email domain surfaces related contacts even when the exact address differs:
```
FIND {masterslaundry.com} IN ALL FIELDS
RETURNING Contact(Id, Name, Email, Phone, Account.Name, Account.LW_account_ID__c)
```
Returned 4 contacts across `American Laundromat` (00138), `Masters Laundry Equipment` (01777),
`Super Saver Laundries` (00410). Good for "is anyone from this shop already in SF?" Treat as weak.

## 6. CUSTID / master-id lookups (STRONGEST — run first when MOOPS gives an id)

```sql
SELECT Id, Name, LW_account_ID__c, Type, BillingCity, BillingState
FROM Account WHERE LW_account_ID__c = '02141'
```
Confirmed → `Washing House` (Id `001S600000XuPiJIAV`), Type Customer, Colleyville TX.

```sql
SELECT Id, Name, Company, Email, Phone, Status, IsConverted, Moops_Customer_id__c, Moops_Location_Key_ID__c
FROM Lead WHERE Moops_Customer_id__c = '00378'
```
Leads carry the MOOPS customer id directly. Sample rows returned (where `Moops_Customer_id__c != null`):
`Wash Rite Laundry` (00378, loc key 6679), `Skyline Laundromats` (01570, 6592),
`New AD Laundromat` (01564, 6570, converted), `Nassau Laundromat` (1595 — unpadded, loc key 0100001).

```sql
SELECT Id, Name, LW_Location_ID__c, Cents_Identifier__c, Account__c, Street__c, City__c, State_Province__c, Phone_Number__c
FROM Custom_Location__c WHERE LW_Location_ID__c = '8235'
```
Locations carry `LW_Location_ID__c`. Pull an account's locations via `WHERE Account__c = '<Account Id>'`
— e.g. Washing House's account → `2150 Josey Lane Ste 106`, Carrollton TX, LW loc `8235`
(`LaundroWorks_Active__c = false`).

## 7. Lead email/phone (STRONG) — runs alongside Contact

```sql
SELECT Id, Name, Company, Email, Phone, Status, IsConverted, Moops_Customer_id__c
FROM Lead WHERE Email = 'jj.dosamigos@gmail.com'
```
Returned `Jeremy Jones / Dos Amigos Wash N Dry 3`, `IsConverted = true` — so Dos Amigos exists as a
**converted Lead** AND as an Account/Contact. Converted ⇒ prefer the Account.

## Lightning links

- Account: `https://trycentssf.lightning.force.com/lightning/r/Account/<Account Id>/view`
- Contact: `https://trycentssf.lightning.force.com/lightning/r/Contact/<Contact Id>/view`

## Interpreting results

- Empty (`totalSize:0` / `searchRecords:[]`) = no match = "new in SF". Not an error.
- `Account.LW_account_ID__c` populated ⇒ this SF account is already linked to an Admin/Laundroworks
  customer; null ⇒ exists in SF (sales pipeline) but not yet provisioned in Laundroworks — a useful
  cross-system gap to flag.
- SOSL is fuzzy by design; always tie-break weak name hits with city/state before asserting a match.
