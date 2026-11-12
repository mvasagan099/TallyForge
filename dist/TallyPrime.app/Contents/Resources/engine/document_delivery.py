"""Explicit, TLS-only SMTP delivery, configured through environment variables."""
from email.message import EmailMessage
from email.utils import parseaddr
import os
import smtplib
import ssl
from engine.data_exchange import export_workbook


def send_workbook(db, recipient, kind):
    parsed = parseaddr(recipient)[1]
    if parsed != recipient or '@' not in parsed or '\n' in recipient or '\r' in recipient:
        raise ValueError('A single valid recipient email address is required')
    host, sender = os.environ.get('TALLY_SMTP_HOST'), os.environ.get('TALLY_SMTP_SENDER')
    user, password = os.environ.get('TALLY_SMTP_USER'), os.environ.get('TALLY_SMTP_PASSWORD')
    port = int(os.environ.get('TALLY_SMTP_PORT', '465'))
    if not host or not sender or not user or not password or port not in (465,587):
        raise ValueError('Configure TLS SMTP host, sender, user, password and port 465/587 in the environment')
    message = EmailMessage()
    message['From'],message['To'] = sender,parsed
    message['Subject'] = db.get_company().get('name','Company')+' — '+kind
    message.set_content(f'{kind} exported from the selected company is attached.')
    message.add_attachment(export_workbook(db,kind),maintype='application',subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',filename=kind.replace(' ','_')+'.xlsx')
    client = smtplib.SMTP_SSL(host,port,timeout=20,context=ssl.create_default_context()) if port==465 else smtplib.SMTP(host,port,timeout=20)
    with client:
        if port==587:
            client.starttls(context=ssl.create_default_context())
        client.login(user,password)
        rejected=client.send_message(message)
    if rejected:
        raise ValueError('SMTP server rejected delivery to the recipient')
    with db.conn:
        db._log_edit('Company',str(db.active_company_id),'EMAIL_EXPORT',f'{kind} sent to {parsed}')
    return {'status':'accepted by SMTP server','recipient':parsed}
