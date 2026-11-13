# Try TallyForge's XML export in TallyPrime (on a test company only)

TallyForge's exported XML has only been checked by importing it back into TallyForge. Whether TallyPrime accepts it is
**not tested**. This takes about ten minutes and never touches your real books.

1. Make a sample file from made-up data (nothing from your company):

   ```
   python tools/make_tallyprime_import_sample.py TallyForge_import_sample.xml
   ```

   It prints the closing balances to expect.
2. In TallyPrime create a **new, empty test company** (never your real one) and open it.
3. Import the file: *Gateway of Tally > Import > Data*, choose the XML file, and select the option that **keeps existing
   data** (do not choose "modify with new data" or "combine opening balances" on a company with real data).
4. Open the Trial Balance and compare with the printed balances. Also check that the Day Book has four vouchers
   (a purchase, a GST sale, a receipt and a payment) and that Stock Summary shows 70 Kg of Sample Rice.
5. Tell me what TallyPrime said: any import errors or exceptions it lists (TallyPrime writes them to *tallyimport.log* in
   its folder), and any figure that differs. I will fix the exporter from that.

Do not import into the company holding your real books.
