# Dedupe blocker rules — questions for Mark

> Purpose: the `moops-dedupe` skill today answers only *identity* (new / existing / potential).
> To run an order straight into SF (Account → Location → Opportunity) without the Slack hand-off,
> the deduper needs to also decide **proceed vs needs-Mark**, and name the blocker. The structure
> for that is ready to add (two-axis output + extensible `blockers[]` + Opportunity/Case queries);
> what's missing is Mark's actual rule for what makes an account "have an issue." These are the
> questions that turn his mental checklist into queries.

## The core question

When you open Salesforce on a dedupe candidate, **what exactly do you look at to decide "fine to
proceed and create" vs "this has an issue, don't auto-create"?** Walk through the last couple you
stopped on — what did you see that made you stop?

## Specific signals to confirm (each maps to one query we'd add)

1. **Cents Location ID** (`Custom_Location__c.Cents_Identifier__c`) — already our one flag: populated
   = live Cents POS, hard to combine, escalate. Confirm that's right, and that blank = safe.

2. **Billing / other id** — Matt recalls "maybe a billing id" as a blocker. Is there a *second*
   identifier (Maxio id, Stripe account, billing account) whose presence means hard-to-combine, like
   the Cents Location ID does? Which field?

3. **Open Opportunity** — the workflow creates a `<addr>-Moops-SO-<#>` Opp. If an Opportunity already
   exists on the account, do we attach to it or stop? **Which stages block** (any open Opp? only
   Closed Won on the same location? a competing AE's open deal)?

4. **Multiple matching accounts (fragmentation)** — e.g. "University Laundromat" / "Super Clean" each
   have many account records. When a customer spans several accounts, how do you pick the canonical
   one, and when is the ambiguity itself a stop?

5. **Onboarding Case** — once the `Onboarding – Laundroworks` Case exists, does an open Case for the
   account/opp mean "already in flight, don't re-onboard"?

6. **Account Type / Status** — does `Type = Customer` always mean attach-not-create? Is there any
   Type/Status value that blocks (e.g. a hold/churned/do-not-contact state)?

7. **LW_account_ID** — confirm: matched Account with `LW_account_ID__c` null = in SF, not yet in
   Laundroworks → safe to provision; populated = already linked.

## The bottom line we're after

What can the deduper **safely clear to run end-to-end without you**, and what is the minimum set of
conditions that must route to you? Everything you *don't* list as a blocker, we automate.
