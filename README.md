# SmartShop Pro 2.0
Windows / VS Code: extract ZIP, open folder, run:
```
python -m pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000/register and create the first owner account (password at least 10 characters). Then login. Add products, customers and shop settings. USB barcode scanners must operate in keyboard mode with Enter suffix. UPI QR is a payment request only; verify receipt manually. GST rates are configurable among common rates; this is not a certified GST invoice system (no CGST/SGST/IGST, HSN or statutory reporting). Sales graphs show recorded invoices, including pending UPI. Backups refresh on login and changes, one snapshot per date. Copy backups off-device regularly.
Existing project: stop old server, preserve old shop.db separately, then copy it over the empty shop.db in this folder if desired. Products are migrated automatically; old sales table is preserved but not included in new invoice reports. Never overwrite an existing database without backup.
Local prototype only: before public deployment add HTTPS, production WSGI, rate limiting, audit logs, stronger account recovery, backup retention and GST compliance. Do not publish .secret or real customer data.
