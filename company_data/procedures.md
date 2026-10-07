# Procedure — Processing an Incoming Invoice

1. Find the vendor's invoice files under `company_data/invoices/`.
2. If asked for the "latest" invoice, compare the `Invoice Date` field of each
   file for that vendor and choose the most recent one.
3. Read the chosen invoice and extract vendor name, amount, and due date.
4. Read `company_data/policies.md`. If the amount is above the approval
   threshold, request human approval before entering anything.
5. Open the payables app's add-invoice page, fill vendor, amount, and due date,
   and submit the form.
6. Ask for verification. Verification must read the payables system's read-only
   API and compare the stored record to the invoice file.
7. Report a short summary with the evidence file paths. Never claim completion
   before verification matches.
