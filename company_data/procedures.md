# Invoice Processing Procedure

1. Find all invoices from the vendor in the `company_data/invoices` folder.
2. Read each invoice and determine which one has the latest due date.
3. Extract the invoice number, vendor, amount and due date.
4. Read `policies.md` and check whether the amount requires approval.
5. Open the payables app at `/invoice/new`.
6. Fill the `invoice_number`, `vendor`, `amount` and `due_date` fields.
7. Submit the form.
8. Confirm the record actually exists by verifying against the read-only API. Only the verification step may declare the task complete.