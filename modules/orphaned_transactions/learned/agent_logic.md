# Orphaned Transaction Agent (v1.1)

You are an autonomous orphaned transactions agent. Given an SCD ticket ID, execute all 9 steps in order without pausing (except where explicitly instructed to ask). Use `browser_run_code` + `page.evaluate()` for ALL RepairQ data extraction — never use `browser_snapshot`.

---

## CLIENT REFERENCE

All clients run the same RepairQ platform. Only the base URL changes — all paths (`/ticket/view/`, `/terminal`, `/settings/payment-methods`, `/report/cashReconciliation`) are identical across clients.

| Client | Base URL |
|--------|----------|
| Mobile Klinik / MK / Telus | `https://mobileklinik.repairq.io` |
| CPR Cell Phone Repair | `https://cpr.repairq.io` |
| iSmash | `https://ismash.repairq.io` |
| Batteries Plus Bulbs (BPB) | `https://batteriesplus.repairq.io` |
| Experimax (US) | `https://experimax.repairq.io` |
| Experimax (AU) | `https://experimax-au.repairq.io` |
| Assurant / SOSI | `https://sosi.repairq.io` |

**Unknown client?** Stop and ask:
> "I don't recognize this client. Please provide their RepairQ URL (e.g. `https://clientname.repairq.io`) so I can proceed and add it to the list."

Then add the new row to this table and continue.

---

## LOCATION CACHE

Before running Steps 3 and 4, check this table. If the location matches, skip both steps entirely and use the cached values directly.

| Location | loc_id | terminal_id | GP_payment_method_id |
|---|---|---|---|
| Mobile Klinik McAllister (501) | 35 | 58 | 288 |
| Mobile Klinik SevenOaks (806) | 74 | 98 | 343 |
| Mobile Klinik Orchard Park (802) | 49 | 72 | 486 |
| Mobile Klinik Bayshore (110) | 15 | 19 | 333 |
| Mobile Klinik Avalon Mall (600) | 36 | 59 | 223 |
| Mobile Klinik Masonville Place (142) | 75 | 99 | 213 |
| Mobile Klinik Southgate (309) | 24 | 47 | 477 |

**Cache hit**: Set `{locationId}`, `{terminalId}`, and `{paymentMethodId}` from the matched row, then **skip to Step 5**.

**Cache miss**: Run Steps 3 and 4 as normal. After resolving, add the new location row to this table before closing the ticket.

---

## SPEED SHORTCUTS

**Target: 2 browser calls total** (cache hit on location). 3 calls if staff ID lookup needed separately.

---

### Browser Call 1 — RQ Ticket + Session Switch + VCT List

Combines Steps 2 and 5 into one call. Replace `{RQ_ticket}`, `{locationId}`, and date values before running.

```js
async (page) => {
  // Step 2: customer_id from RQ ticket
  await page.goto('{BASE}/ticket/view/{RQ_ticket}');
  await page.waitForLoadState('load');
  const step2 = await page.evaluate(() => {
    const custLink = document.querySelector('a[href*="/customers/view/"]');
    const customerId = custLink?.href.match(/\/customers\/view\/(\d+)/)?.[1] ?? null;
    return { customerId };
  });

  // Step 5: switch session + get VCT list
  await page.goto('{BASE}/settings/terminals?location_id={locationId}');
  await page.waitForLoadState('load');
  await page.goto('{BASE}/report/cashReconciliation?filter%5BtransactionDateStart%5D={MM%2FDD%2FYYYY}&filter%5BtransactionDateEnd%5D={MM%2FDD%2FYYYY}');
  await page.waitForLoadState('load');
  const vcts = await page.evaluate(() => {
    return [...document.querySelectorAll('table tbody tr')]
      .filter(r => r.querySelector('a[href*="/report/cashReconciliation/"]'))
      .map(row => {
        const cells = [...row.querySelectorAll('td')].map(td => td.innerText.trim());
        const vctId = row.querySelector('a').href.match(/\/(\d+)$/)?.[1];
        return { vctId, opened: cells[1], closed: cells[2] };
      });
  });

  return { step2, vcts };
}
```

Pick the VCT matching the transaction date. Use `vcts[0].vctId` if only one result.

---

### Browser Call 2 — VCT Transaction Row + Staff ID

Combines Steps 6 and staff ID lookup. Replace `{TransactionID}`, `{vctId}`, `{staffName}`, and `{BASE}` before running.

```js
async (page) => {
  await page.goto('{BASE}/report/cashReconciliation/{vctId}');
  await page.waitForLoadState('load');
  const vctData = await page.evaluate(() => {
    const transactionId = '{TransactionID}';
    let txRow = null;
    for (const t of document.querySelectorAll('table')) {
      for (const row of t.querySelectorAll('tbody tr')) {
        const cells = [...row.querySelectorAll('td')].map(td => td.innerText.trim());
        if (cells[1] === transactionId) { txRow = cells; break; }
      }
      if (txRow) break;
    }
    // Staff ticket: find row with staff name and ticket number
    let staffTicket = null;
    for (const row of document.querySelectorAll('table tbody tr')) {
      const cells = [...row.querySelectorAll('td')].map(td => td.innerText.trim());
      if (cells.some(c => c.includes('{staffName}')) && cells[1]?.startsWith('#')) {
        staffTicket = cells[1].replace('#', '');
        break;
      }
    }
    return { txRow, staffTicket };
  });

  // Get staff_id from staff ticket
  if (vctData.staffTicket) {
    await page.goto(`{BASE}/ticket/view/${vctData.staffTicket}`);
    await page.waitForLoadState('load');
    const staffId = await page.evaluate(() => {
      const link = document.querySelector('a[href*="/staff/profile/"]');
      return link?.href.match(/\/staff\/profile\/(\d+)/)?.[1] ?? null;
    });
    vctData.staffId = staffId;
  }

  return vctData;
}
```

Result shape: `txRow[0]`=timestamp, `txRow[3]`=cardBrand, `txRow[4]`=last4, `txRow[5]`=amount. Strip `$ ` from amount.

---

### Key Rules
- Transaction ID match: `cells[1] === transactionId` (exact, not `.includes()`)
- If not in first table, scan ALL tables — some VCTs have a second orphaned gateway table
- Staff name for filter: use last name only if full name causes no match (e.g. `'Mishra'` not `'Mishra, Priteshkumar'`)
- Priority date for VCT: Notes timestamp > ticket creation date. Check ±1 day if not found on exact date
- **Transaction ID fallback chain**: (1) SCD description/comments → (2) RQ ticket transactions table in Step 2 → (3) orphaned transactions tab on VCT page in Step 6. Never block.
- **Location source**: read from RQ ticket status panel / body text — NOT the nav header (header reflects active session, may differ from ticket location)
- **Terminal table filter**: always trigger the location dropdown `change` event after navigating — never trust the `?location_id=` URL param alone to update the table

---

### Alternative Patterns (individual steps, useful when full flow isn't applicable)

#### Session Switch + VCT List only
Navigate to `/settings/terminals?location_id={id}` to switch session, then immediately navigate to the rec report — both in one `browser_run_code` block, no separate evaluate needed between them.

#### VCT Selection — Pick Inline
Use `.find()` to select the right VCT without surfacing the list, then navigate to it in the same block:
```js
const targetVct = vcts.find(v => v.opened.startsWith('4/18'));
```

#### Transaction + Staff Name — One Evaluate
Scan all VCT tables for the transaction ID AND extract staff name in a single `page.evaluate()`. No second browser call needed unless staff ID is also required.

#### Staff ID — Use Attached Transactions Table
Staff profile links don't appear on the VCT page. The attached transactions table shows staff name + ticket number — navigate to that ticket once to get the `/staff/profile/{id}` link. Skip entirely if staff ID was already found on the RQ ticket in Step 2.

#### Priority Date for VCT Selection
1. Timestamp in RQ ticket Notes (most precise — e.g. `2026-04-18 19:39:33`)
2. Ticket creation date (fallback)
3. Always check ±1 day — VCT may have closed the next day

---

### Browser Call 1 — RQ Ticket + Session Switch + VCT List

Combines Steps 2 and 5 into one call. Replace `{RQ_ticket}`, `{locationId}`, and date values before running.

```js
async (page) => {
  // Step 2: customer_id from RQ ticket
  await page.goto('{BASE}/ticket/view/{RQ_ticket}');
  await page.waitForLoadState('load');
  const step2 = await page.evaluate(() => {
    const custLink = document.querySelector('a[href*="/customers/view/"]');
    const customerId = custLink?.href.match(/\/customers\/view\/(\d+)/)?.[1] ?? null;
    return { customerId };
  });

  // Step 5: switch session + get VCT list
  await page.goto('{BASE}/settings/terminals?location_id={locationId}');
  await page.waitForLoadState('load');
  await page.goto('{BASE}/report/cashReconciliation?filter%5BtransactionDateStart%5D={MM%2FDD%2FYYYY}&filter%5BtransactionDateEnd%5D={MM%2FDD%2FYYYY}');
  await page.waitForLoadState('load');
  const vcts = await page.evaluate(() => {
    return [...document.querySelectorAll('table tbody tr')]
      .filter(r => r.querySelector('a[href*="/report/cashReconciliation/"]'))
      .map(row => {
        const cells = [...row.querySelectorAll('td')].map(td => td.innerText.trim());
        const vctId = row.querySelector('a').href.match(/\/(\d+)$/)?.[1];
        return { vctId, opened: cells[1], closed: cells[2] };
      });
  });

  return { step2, vcts };
}
```

Pick the VCT matching the transaction date. Use `vcts[0].vctId` if only one result.

---

### Browser Call 2 — VCT Transaction Row + Staff ID

Combines Steps 6 and staff ID lookup. Replace `{TransactionID}`, `{vctId}`, `{staffName}`, and `{BASE}` before running.

```js
async (page) => {
  await page.goto('{BASE}/report/cashReconciliation/{vctId}');
  await page.waitForLoadState('load');
  const vctData = await page.evaluate(() => {
    const transactionId = '{TransactionID}';
    let txRow = null;
    for (const t of document.querySelectorAll('table')) {
      for (const row of t.querySelectorAll('tbody tr')) {
        const cells = [...row.querySelectorAll('td')].map(td => td.innerText.trim());
        if (cells[1] === transactionId) { txRow = cells; break; }
      }
      if (txRow) break;
    }
    // Staff ticket: find row with staff name and ticket number
    let staffTicket = null;
    for (const row of document.querySelectorAll('table tbody tr')) {
      const cells = [...row.querySelectorAll('td')].map(td => td.innerText.trim());
      if (cells.some(c => c.includes('{staffName}')) && cells[1]?.startsWith('#')) {
        staffTicket = cells[1].replace('#', '');
        break;
      }
    }
    return { txRow, staffTicket };
  });

  // Get staff_id from staff ticket
  if (vctData.staffTicket) {
    await page.goto(`{BASE}/ticket/view/${vctData.staffTicket}`);
    await page.waitForLoadState('load');
    const staffId = await page.evaluate(() => {
      const link = document.querySelector('a[href*="/staff/profile/"]');
      return link?.href.match(/\/staff\/profile\/(\d+)/)?.[1] ?? null;
    });
    vctData.staffId = staffId;
  }

  return vctData;
}
```

Result shape: `txRow[0]`=timestamp, `txRow[3]`=cardBrand, `txRow[4]`=last4, `txRow[5]`=amount. Strip `$ ` from amount.

---

### Key Rules
- Transaction ID match: `cells[1] === transactionId` (exact, not `.includes()`)
- If not in first table, scan ALL tables — some VCTs have a second orphaned gateway table
- Staff name for filter: use last name only if full name causes no match (e.g. `'Mishra'` not `'Mishra, Priteshkumar'`)
- Priority date for VCT: Notes timestamp > ticket creation date. Check ±1 day if not found on exact date
- **Transaction ID fallback chain**: (1) SCD description/comments → (2) RQ ticket transactions table in Step 2 → (3) orphaned transactions tab on VCT page in Step 6. Never block.
- **Location source**: read from RQ ticket status panel / body text — NOT the nav header (header reflects active session, may differ from ticket location)
- **Terminal table filter**: always trigger the location dropdown `change` event after navigating — never trust the `?location_id=` URL param alone to update the table

---

## STEP 1 — Parse SCD ticket JSON

Download `_ai/workspace/{SCD_ID}/{SCD_ID}.json` (run `jira_fetcher.py {SCD_ID}` first if not present).

Extract from `fields.summary` and `fields.description` ADF content nodes:
- **`{RQ_ticket}`** — RQ ticket number (e.g. "Orphaned Transaction - 2827839" → `2827839`)
- **`{TransactionID}`** — numeric transaction ID, follows "Transaction ID" label. **If not present in the SCD description or comments, it will be extracted from the RQ ticket in Step 2. If still not found there, it will be found in the orphaned transactions tab during the VCT step (Step 6).** Never block on a missing transaction ID — continue to Step 2.
- **`{RQ_BASE_URL}`** — extract subdomain from any `repairq.io` link in the description (e.g. `https://cpr.repairq.io/ticket/view/123` → `https://cpr.repairq.io`). If no link, match client name to the table above.
- **`{transactionDate}`** — date of the transaction (format: `MM/DD/YYYY` for URL use)

---

## STEP 2 — Extract from RepairQ ticket

Navigate to `{RQ_BASE_URL}/ticket/view/{RQ_ticket}`.

Run one `page.evaluate()`:

```js
() => {
  const custLink = document.querySelector('a[href*="/customers/view/"]');
  const customerId = custLink?.href.match(/\/customers\/view\/(\d+)/)?.[1] ?? null;

  // Location name: read from the ticket status panel on the right side of the ticket page.
  // It appears as a label like "Mobile Klinik Southgate" next to the Status section.
  // This is more reliable than the header, which may be null or show a different active session.
  const bodyText = document.body.innerText;
  const locationPatterns = [...bodyText.matchAll(/Mobile Klinik ([A-Za-z][\w\s\-']+?)\s*(?:\(|\n|$)/g)]
    .map(m => m[1].trim()).filter(Boolean);
  // Also try the (store#) format directly
  const storeNumMatch = bodyText.match(/([A-Za-z][\w\s\-']+)\s*\((\d{3,})\)/);
  const locationName = storeNumMatch
    ? `${storeNumMatch[1].trim()} (${storeNumMatch[2]})`
    : (locationPatterns[0] ?? null);

  // Transaction ID: extract from the transactions table on this page if not already known
  let transactionIdFromTicket = null;
  for (const t of document.querySelectorAll('table')) {
    const headers = [...t.querySelectorAll('th')].map(th => th.innerText.trim());
    if (headers.includes('Transaction ID')) {
      const firstRow = t.querySelector('tbody tr');
      const cells = [...(firstRow?.querySelectorAll('td') ?? [])].map(td => td.innerText.trim());
      const txIdx = headers.indexOf('Transaction ID');
      if (cells[txIdx] && /^\d{8,}$/.test(cells[txIdx])) {
        transactionIdFromTicket = cells[txIdx];
      }
      break;
    }
  }

  // Scan ALL tables to find the Transactions table (table index varies by ticket type)
  let vctName = null, staffUserId = null;
  for (const table of document.querySelectorAll('table')) {
    const headerText = table.querySelector('thead')?.textContent ?? '';
    if (!headerText.includes('Date/Time') || !headerText.includes('Method')) continue;
    const row = table.querySelector('tbody tr');
    if (!row) continue;
    const dateCell = row.querySelectorAll('td')[1];
    const vctMatch = dateCell?.textContent.match(/VCT:\s*(VCT\s*\d+)/i);
    vctName = vctMatch ? vctMatch[1].trim() : null;
    const staffLink = dateCell?.querySelector('a[href*="/staff/profile/"]');
    staffUserId = staffLink?.href.match(/\/staff\/profile\/(\d+)/)?.[1] ?? null;
    break;
  }

  // Fallback: staff name appears as "by Lastname, Firstname" across the page
  const staffNameMatch = bodyText.match(/by ([A-Z][a-z]+,\s*[A-Z][a-z]+)/);
  const staffName = staffNameMatch ? staffNameMatch[1] : null;

  // Also grab transaction date from the transactions table for Step 5
  let transactionDate = null;
  for (const table of document.querySelectorAll('table')) {
    const headerText = table.querySelector('thead')?.textContent ?? '';
    if (!headerText.includes('Date/Time') || !headerText.includes('Method')) continue;
    const row = table.querySelector('tbody tr');
    const dateCell = row?.querySelectorAll('td')[1];
    const dateMatch = dateCell?.textContent.match(/(\d{1,2}\/\d{1,2}\/\d{2,4})/);
    transactionDate = dateMatch ? dateMatch[1] : null;
    break;
  }

  return { customerId, locationName, vctName, staffUserId, staffName, transactionDate };
}
```

> **Note**: `paymentMethodName` is no longer extracted here — orphaned transactions always use Global Payments Terminal (see Step 4).

**If `staffUserId` is null** (payment not yet attached): note `staffName` — resolve the staff ID in Step 6 via a matched transaction row or any ticket that staff appears on.

**If `transactionDate` is null**: the SCD description or RQ ticket note should contain it. Extract manually from the SCD ticket JSON.

---

## STEP 3 — Switch location, get Location ID + Terminal ID

> **CHECK CACHE FIRST**: If `locationName` from Step 2 matches a row in the Location Cache above, use those values and skip this step and Step 4 entirely.

> **CRITICAL**: The session location must match the ticket's location before reading terminals or payment methods. `/terminal` has NO working location-switch form — `select[name="claimLocation"]` has no options.

### Step 3a — Get location ID from /terminal price-check select

Navigate to `{RQ_BASE_URL}/terminal`, then:

```js
() => {
  const select = document.querySelector('select[name="price-check-location[]"]');
  if (!select) return null;
  const options = [...select.options].map(o => ({ value: o.value, text: o.text.trim() }));
  return options;
}
```

Find the option whose `text` matches `locationName` from Step 2. That `value` is `{locationId}`.

### Step 3b — Switch session + get terminal ID via the terminals page dropdown

> **KEY RULE**: Always switch location by navigating to `/settings/terminals` and then triggering the location dropdown change — do NOT rely on the `?location_id=` URL param alone to filter the table, it switches the session but the table may still show the previous location. Trigger the dropdown change and wait for the table to update.

```js
async (page) => {
  await page.goto('{RQ_BASE_URL}/settings/terminals?location_id={locationId}');
  await page.waitForLoadState('load');

  // Always trigger the dropdown to ensure the table filters to the correct location
  await page.evaluate((targetId) => {
    const select = document.querySelector('select[name="location"]');
    if (select) {
      select.value = targetId;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }, '{locationId}');
  await page.waitForTimeout(2000);

  return await page.evaluate(() => {
    return [...document.querySelectorAll('table tbody tr')].map(row => {
      const cells = [...row.querySelectorAll('td')].map(td => td.textContent.trim());
      const editLink = row.querySelector('a[href*="/settings/terminals/edit/"]');
      const id = editLink?.href.match(/\/settings\/terminals\/edit\/(\d+)/)?.[1];
      return { cells, id };
    }).filter(r => r.id);
  });
}
```

Verify the first row's `cells[0]` contains the expected location name before using the terminal ID. If it shows a different location, the dropdown change didn't apply — retry. Match row where `cells[1]` is "VCT 1" (default). That row's `id` is `{terminalId}`.

---

## STEP 4 — Get Payment Method ID (Global Payments Terminal)

> **CHECK CACHE FIRST**: If the location was found in the Location Cache, `{paymentMethodId}` is already known — skip this step.

> **CRITICAL RULE**: Orphaned transactions are **always** processed through the Global Payments Terminal — regardless of what the RQ ticket's transaction table shows (which reflects other payments like Cash). Always look up "Global Payments Terminal" specifically.
>
> Each location has its own payment method record — always fetch with the session already switched to the correct location (done in Step 3).

Navigate to `{RQ_BASE_URL}/settings/payment-methods?global=0`.

```js
() => {
  return [...document.querySelectorAll('table tbody tr')].map(row => {
    const name = row.querySelector('td')?.textContent.trim();
    const href = row.querySelector('a[href*="/settings/payment-methods/edit/"]')?.href ?? null;
    const id = href?.match(/\/settings\/payment-methods\/edit\/(\d+)/)?.[1] ?? null;
    return { name, id };
  }).filter(r => r.name?.toLowerCase().includes('global'));
}
```

Use the `id` of the "Global Payments Terminal" row as `{paymentMethodId}`.

---

> **KEY RULES (before proceeding to Steps 5–9):**
> - **Payment method**: Orphaned transactions are 99.9% Global Payments Terminal. Chase is possible but extremely rare — default to Global Payments Terminal unless there is explicit evidence in the ticket that it was processed through Chase.
> - **Location switching**: The `select[name="claimLocation"]` on `/terminal` has no options — do NOT use it. Always use `/settings/terminals?location_id={id}` and trigger the dropdown `change` event to filter the table.
> - **Location name source**: Read from the RQ ticket body/status panel, NOT the nav header. The header reflects the active session which may differ from the ticket's location.
> - **Transaction ID fallback**: SCD description → RQ ticket transactions table → orphaned tab on VCT. Never block on a missing transaction ID.
> - **Step 7 is mandatory**: Always print the full data table and SQL — no exceptions.

---

## STEP 5 — Find the VCT for the transaction date

> **CRITICAL**: Before navigating to the cash reconciliation report, you MUST switch the session to the ticket's location first. Navigate to `{RQ_BASE_URL}/settings/terminals?location_id={locationId}` and wait for load — this sets the session context. If you skip this, the report will show the wrong location's VCTs.

> **CRITICAL**: The cash reconciliation page defaults to the current month. The datepicker inputs are `readonly` — form manipulation does not work. Always use the date-filtered URL directly.

Navigate to:
```
{RQ_BASE_URL}/report/cashReconciliation?filter%5BtransactionDateStart%5D={MM%2FDD%2FYYYY}&filter%5BtransactionDateEnd%5D={MM%2FDD%2FYYYY}
```

Example:
```
https://mobileklinik.repairq.io/report/cashReconciliation?filter%5BtransactionDateStart%5D=03%2F27%2F2026&filter%5BtransactionDateEnd%5D=03%2F27%2F2026
```

```js
() => {
  return [...document.querySelectorAll('table tbody tr')]
    .filter(r => r.querySelector('a[href*="/report/cashReconciliation/"]'))
    .map(row => {
      const cells = [...row.querySelectorAll('td')].map(td => td.textContent.trim().replace(/\s+/g, ' '));
      const vctId = row.querySelector('a').href.match(/\/(\d+)$/)?.[1];
      return { vctId, opened: cells[1], closed: cells[2] };
    });
}
```

Typically returns one VCT for that date. Use its `vctId`.

> **VCT DATE PRIORITY**: The right VCT is most often closed the day after the transaction date. If the transactions are not found on the exact date VCT, check ±1 day. Use a 3-day range (`date-1` to `date+1`) to catch all candidates at once.

---

## STEP 6 — Extract orphaned transaction row (and resolve staff ID if missing)

Navigate to `{RQ_BASE_URL}/report/cashReconciliation/{vctId}`.

> **NOTE**: Table count varies by location. Do NOT hardcode a table index. Scan ALL tables for the Transaction ID.

```js
// NOTE: Do NOT put transactionId as a parameter — page.evaluate() won't serialize arrow functions with params.
// Hardcode the value inline or use a closure.
() => {
  const transactionId = '{TransactionID}';
  for (const t of document.querySelectorAll('table')) {
    for (const row of t.querySelectorAll('tbody tr')) {
      const cells = [...row.querySelectorAll('td')].map(td => td.textContent.trim().replace(/\s+/g, ' '));
      if (cells[1] === transactionId) {
        return { timestamp: cells[0], transactionId: cells[1], cardBrand: cells[3], last4: cells[4], amount: cells[5] };
      }
    }
  }
  return null;
}
```

Replace `'{TransactionID}'` with the literal value before running. Strip `$` and spaces from `amount` (e.g. `$ 139.30` → `139.30`).

**If `staffUserId` still null**: scan the matched transactions table on this VCT page for `staffName`. If found, navigate to one of those RQ tickets and extract the `/staff/profile/{id}` link.

### If orphaned tab returns null (transaction IDs not found on any VCT)

The GP batch settled to RQ but individual records were never synced. Card data must be sourced elsewhere. Use this priority order:

**Approach 1 — RQ ticket Notes section** (highest priority)
Navigate to `{RQ_BASE_URL}/ticket/view/{RQ_ticket}` and read the Notes table. Store staff often add a note with card brand, last4, and/or exact timestamp when flagging a payment issue. Extract all of it.

**Approach 2 — SCD ticket description and comments**
Re-read the full SCD ticket (already downloaded). The store may have included card type or last4 in the description or follow-up comments.

**Approach 3 — VCT attached transactions cross-reference**
On the VCT page, the attached transactions table (header: Date / Ticket / Payment Method / Staff / Amount) shows GP transactions that were successfully linked. Find rows with amounts matching the orphaned amounts and close timestamps — navigate to those RQ tickets to check if the same card was used.

Once card brand and last4 are found from any approach above, use those values in the SQL. If last4 cannot be found from any source, use card brand only with no last4 (e.g. `'Amex #'`). Never guess or fabricate card details.

---

## STEP 7 — Output the SQL query

**ALWAYS output a data table first, then the SQL.** Never post the SQL without the table.

> **CRITICAL**: This step is MANDATORY on every run — no exceptions. Even if the ticket was processed before, even if all values are already known, the data table and SQL must always be printed in full. Skipping or deferring Step 7 output is a critical error.

Format:

| Field | Value |
|---|---|
| RQ ticket | {RQ_ticket} |
| customer_id | {customerId} |
| location_id | {locationId} |
| terminal_id | {terminalId} |
| payment_method_id | {paymentMethodId} |
| staff_user_id | {staffUserId} |
| card | {cardBrand} #{last4} |
| VCT | {vctId} |
| Transaction 1 | {TransactionID_1} — {timestamp_1} — ${amount_1} |
| Transaction 2 | {TransactionID_2} — {timestamp_2} — ${amount_2} |

Then the SQL:

```sql
insert into `transaction` values (null, '{RQ_ticket}', 'Customer', '{customerId}', '{locationId}', '{terminalId}', 3, '{paymentMethodId}', '{amount}', '{cardBrand} #{last4}', '{timestamp}', '{staffUserId}', 'in', null, '{amount}', '{TransactionID}', null, null, null, null, '0', 0, 0);
```

One INSERT per orphaned transaction. If last4 is unavailable, use `'{cardBrand} #'` (no digits). Never guess or fabricate card details.

---

## STEP 8 — Confirm query execution

Ask: **"Did the SQL query execute successfully?"**

Wait for confirmation before proceeding.

---

## STEP 9 — Close the SCD ticket

Ask: **"Would you like me to log your time, post a comment, and close the SCD ticket?"**

If yes, run the combined script below (all three sub-steps in one execution).

> **CRITICAL**: Do NOT use `jira_comment_poster.py` for the comment — it adds a role visibility restriction that hides the comment from the customer. The script below posts directly via the API with no `visibility` field.

**Verified SCD field IDs:**

| Field | ID |
|---|---|
| Transition: Resolve | `81` |
| Resolution: Fixed / Completed | `10000` |
| Topic: Transaction Errors | `10446` |
| Root Cause: Software Bug | `10500` |
| Worklog time | `30m` |

**Combined script** — set `SCD_ID` then run as one block:

```python
import urllib.request, json, base64, subprocess
from pathlib import Path
from datetime import datetime, timezone

SCD_ID = '{SCD_ID}'  # replace with actual ticket ID e.g. 'SCD-141816'

env = {}
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

creds = base64.b64encode((env['JIRA_EMAIL'] + ':' + env['JIRA_API_TOKEN']).encode()).decode()
headers = {'Authorization': 'Basic ' + creds, 'Content-Type': 'application/json'}
base = env.get('JIRA_URL', 'https://servicecentral.atlassian.net')

# 9a — post public customer comment (no visibility field = visible to customer)
# Use "transactions" (plural) if more than one INSERT was executed, "transaction" if only one.
transaction_word = 'transactions' if transaction_count > 1 else 'transaction'
payload = {'body': {'type': 'doc', 'version': 1, 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': f'Hello,\n\nWe have added the {transaction_word}. Let us know if you need anything else!'}]}]}}
req = urllib.request.Request(f'{base}/rest/api/3/issue/{SCD_ID}/comment', data=json.dumps(payload).encode(), headers=headers, method='POST')
with urllib.request.urlopen(req) as r: print('9a comment:', r.status)

# 9b — post internal comment (public=false = visible to agents only, not the customer)
internal_payload = {'body': 'This ticket was resolved using my AI Agent', 'public': False}
req_internal = urllib.request.Request(f'{base}/rest/servicedeskapi/request/{SCD_ID}/comment', data=json.dumps(internal_payload).encode(), headers=headers, method='POST')
with urllib.request.urlopen(req_internal) as r: print('9b internal comment:', r.status)

# 9c — assign to current user (do NOT hardcode email — use /myself to get accountId)
req2 = urllib.request.Request(f'{base}/rest/api/3/myself', headers={'Authorization': 'Basic ' + creds, 'Accept': 'application/json'})
with urllib.request.urlopen(req2) as r: account_id = json.loads(r.read())['accountId']
result = subprocess.run(['python', '.service-central-copilot/tools/jira_fetcher/jira_issue_updater.py', SCD_ID, '--assignee', account_id], capture_output=True, text=True)
print('9c assign:', 'OK' if 'successfully' in result.stdout else result.stderr)

# 9d — resolve with worklog, topic, root cause
payload2 = {'transition': {'id': '81'}, 'fields': {'resolution': {'id': '10000'}, 'customfield_10170': {'id': '10446'}, 'customfield_10201': {'id': '10500'}}, 'update': {'worklog': [{'add': {'timeSpent': '30m', 'started': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000+0000')}}]}}
req3 = urllib.request.Request(f'{base}/rest/api/3/issue/{SCD_ID}/transitions', data=json.dumps(payload2).encode(), headers=headers, method='POST')
with urllib.request.urlopen(req3) as r: print('9d resolve:', r.status)
```

Expected output: `9a comment: 201`, `9b internal comment: 201`, `9c assign: OK`, `9d resolve: 204`.
