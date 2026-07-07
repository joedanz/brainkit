# =============================================================================
# Phase-1 Quick-Win Cron Jobs — Luxury Travel Atelier
# -----------------------------------------------------------------------------
# Hermes parses plain-English when you ask conversationally; the scheduler
# itself stores one of: relative ('2h'), interval ('every 1d'), 5-field cron
# ('0 9 * * 1-5'), or ISO timestamp. Below each job is given TWO ways:
#   (A) the conversational request (what you'd type to Hermes in chat), and
#   (B) the exact `hermes cron create "<schedule>" "<prompt>" [flags]` CLI form
#       using only documented flags (--skill, --name).
# Delivery channel is stated inside the prompt (robust across versions); to pin
# it structurally use the `cronjob` tool's deliver= argument from within a skill.
# All jobs are READ + DRAFT only — nothing is sent or booked without an advisor.
# Provider note: jobs fail-closed if the global default model changes, so pin a
# frontier model (Opus/Sonnet) as the desk default before enabling these.
# =============================================================================

# 1) Morning desk briefing — every weekday at 08:00
# (A) "Every weekday at 8am, build the desk briefing: today's departures and
#      arrivals, any balance-due bookings in the next 14 days, VIP clients in
#      transit, and anything needing an advisor decision. Post it to the
#      #travel-desk Slack channel. Read-only."
hermes cron create "0 8 * * 1-5" "Build the morning desk briefing from dossier.db: today's departures/arrivals, bookings with deposit_due_date or balance_due_date within 14 days, VIP/active trips in_progress, and any open items needing an advisor decision. Post to the #travel-desk Slack channel. Read-only; do not message clients." --skill briefing --name "desk-briefing"

# 2) Passport / visa / insurance expiry watch — Mondays at 09:00
# (A) "Every Monday at 9am, check v_documents_expiring_soon for passports,
#      visas and insurance expiring within 9 months and draft a renewal nudge
#      per affected household for advisor review on Slack. Don't email clients."
hermes cron create "0 9 * * 1" "Query v_documents_expiring_soon in dossier.db for passports/visas/insurance within 9 months of expiry. For each affected household draft a discreet renewal-reminder note (Gmail draft, not sent) and summarise the list to #travel-desk for advisor review. Never expose document numbers." --skill briefing --name "document-expiry-watch"

# 3) Payment milestone reminders — daily at 07:30
# (A) "Every day at 7:30am, find bookings with a deposit or balance due in the
#      next 14 days and draft client payment reminders for advisor approval."
hermes cron create "30 7 * * *" "From dossier.db bookings, list any deposit_due_date or balance_due_date falling within the next 14 days (status not cancelled). Draft a courteous payment-reminder email per client as a Gmail DRAFT (do not send) and post the list to #travel-desk." --name "payment-milestone-reminders"

# 4) Pre-trip readiness check — daily at 06:00
# (A) "Each morning at 6am, for trips starting in 14, 7 or 3 days, check the
#      dossier for missing transfers, unconfirmed bookings, or missing travel
#      docs and flag the gaps to the assigned advisor."
hermes cron create "0 6 * * *" "For trips in dossier.db with start_date 14, 7, or 3 days out, verify each has confirmed flights, transfers, accommodation, and required travel documents on file. List any gaps per trip and post to #travel-desk tagging the assigned advisor. Read-only." --skill briefing --name "pre-trip-readiness"

# 5) Commission reconciliation chase — Fridays at 16:00
# (A) "Every Friday at 4pm, find commission_ledger rows that are projected or
#      invoiced and past their expected_date, and draft supplier chase emails
#      for me to review."
hermes cron create "0 16 * * 5" "In dossier.db commission_ledger, find rows with status in ('projected','invoiced') where expected_date has passed. Group by supplier, draft a polite chase email per supplier as a Gmail DRAFT (do not send), and post a reconciliation summary (total outstanding by supplier) to #travel-desk." --name "commission-reconciliation-chase"

# 6) Supplier reliability refresh — 1st of each month at 02:00
# (A) "On the first of every month at 2am, recompute supplier reliability
#      scores from the last quarter's bookings and incidents and write them to
#      the dossier."
hermes cron create "0 2 1 * *" "Recompute supplier reliability for the trailing quarter from dossier.db bookings (on-time/changed/cancelled) and logged incidents; insert one supplier_reliability_scores row per supplier (score 0-100, sample_size, on_time_rate, complaint_rate, methodology). Post the top movers to #travel-desk. This is the only write-job; do not message clients." --name "supplier-reliability-refresh"

# 7) Post-trip follow-up — daily at 10:00
# (A) "Each day at 10am, for trips that ended yesterday, draft a thank-you and
#      a short feedback request for advisor approval."
hermes cron create "0 10 * * *" "For trips in dossier.db with status completed (or end_date = yesterday), draft a warm, understated thank-you plus a brief feedback request per lead client as a Gmail DRAFT (do not send) and list them in #travel-desk for advisor approval." --name "post-trip-followup"

# 8) Flight disruption watch — every 6 hours
# (A) "Every 6 hours, check the GDS for schedule changes on any confirmed
#      flights departing in the next 72 hours and alert me if anything moved."
hermes cron create "every 6h" "Via the GDS MCP, check confirmed/ticketed flight bookings in dossier.db departing within 72 hours for schedule changes, cancellations, or equipment swaps. If anything changed, alert #travel-desk with the affected client, trip, and new times. If nothing changed, reply [SILENT]." --name "flight-disruption-watch"
