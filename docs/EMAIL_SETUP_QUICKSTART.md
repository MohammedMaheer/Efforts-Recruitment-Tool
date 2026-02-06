# Email Setup Quickstart - Gmail & MS365 Outlook

## 🚀 Quick Setup (5 minutes)

Your primary Gmail account is already configured! To add more accounts or set up Outlook:

---

## ✅ Gmail Setup (Already Configured)

**Primary Account:** mohdmaahir786@gmail.com ✅  
**Status:** Ready to use

### Add More Gmail Accounts

1. **Generate App Password**
   - Go to: https://myaccount.google.com/apppasswords
   - Sign in to the Gmail account
   - Click "Generate" → Select "Mail" + "Windows Computer"
   - Copy the 16-character password (e.g., `abcd efgh ijkl mnop`)

2. **Add to `.env` file**
   ```env
   EMAIL_1_SERVER=imap.gmail.com
   EMAIL_1_PORT=993
   EMAIL_1_ADDRESS=your-email@gmail.com
   EMAIL_1_PASSWORD=abcdefghijklmnop  # Remove spaces
   ```

---

## 📧 MS365 Outlook Setup

### Option 1: Simple IMAP (Recommended)

**Works for:** Personal Outlook, Work/School accounts

1. **Use your Outlook email and password**
   ```env
   EMAIL_2_SERVER=outlook.office365.com
   EMAIL_2_PORT=993
   EMAIL_2_ADDRESS=your-email@outlook.com
   EMAIL_2_PASSWORD=YourOutlookPassword
   ```

2. **Enable IMAP in Outlook** (if not already enabled)
   - Go to: https://outlook.office365.com/mail/options/mail/accounts
   - Settings → View all Outlook settings → Mail → Sync email
   - Check "Let devices and apps use IMAP"
   - Save

### Option 2: App Password (More Secure)

1. **Generate App Password**
   - Go to: https://account.microsoft.com/security
   - Advanced security options → App passwords
   - Create a new app password
   - Copy the password

2. **Use in `.env`**
   ```env
   EMAIL_2_SERVER=outlook.office365.com
   EMAIL_2_PORT=993
   EMAIL_2_ADDRESS=your-email@outlook.com
   EMAIL_2_PASSWORD=AppPasswordHere
   ```

---

## 🔧 Common IMAP Servers

| Email Provider | IMAP Server | Port |
|---|---|---|
| **Gmail** | imap.gmail.com | 993 |
| **MS365/Outlook** | outlook.office365.com | 993 |
| **Outlook.com** | outlook.office365.com | 993 |
| **Yahoo** | imap.mail.yahoo.com | 993 |
| **iCloud** | imap.mail.me.com | 993 |
| **Zoho** | imap.zoho.com | 993 |

---

## 🎯 Testing Your Setup

1. **Start the backend**
   ```bash
   cd backend
   python main.py
   ```

2. **Check logs for**
   ```
   ✅ Connected to mohdmaahir786@gmail.com
   📧 Loaded 1 email account(s)
   ```

3. **Enable auto-scraping** (uncomment in `main.py` line 87)
   ```python
   scraper_task = asyncio.create_task(scraper_service.run_continuous_scraper())
   ```

---

## 📁 Email Folders Monitored

By default, the system checks these folders:
- ✅ INBOX
- ✅ Applications  
- ✅ Careers
- ✅ Jobs
- ✅ Resumes

**Change in `.env`:**
```env
EMAIL_FOLDERS_TO_MONITOR=INBOX,Applications,Careers,Custom Folder
```

---

## ⚠️ Troubleshooting

### Gmail: "Invalid credentials"
- ✅ Use **App Password**, NOT regular password
- ✅ Generate at: https://myaccount.google.com/apppasswords
- ✅ Remove spaces from password (e.g., `abcd efgh` → `abcdefgh`)
- ✅ Enable 2-Factor Authentication (required for app passwords)

### Outlook: "Authentication failed"
- ✅ Check IMAP is enabled in settings
- ✅ Try app password instead of regular password
- ✅ For work accounts, check with IT if IMAP is allowed
- ✅ Verify server: `outlook.office365.com` NOT `imap-mail.outlook.com`

### "Connection timed out"
- ✅ Check firewall/antivirus blocking port 993
- ✅ Verify internet connection
- ✅ Try different network (some corporate networks block IMAP)

---

## 🔒 Security Best Practices

1. **Always use App Passwords** (not regular passwords)
2. **Different app password per device/app**
3. **Revoke unused app passwords regularly**
4. **Enable 2-Factor Authentication** on all accounts
5. **Don't share `.env` file** (contains passwords)

---

## 💡 Pro Tips

### Multiple Gmail Accounts
```env
# Personal Gmail
IMAP_SERVER=imap.gmail.com
EMAIL_ADDRESS=personal@gmail.com
EMAIL_PASSWORD=apppassword1

# Work Gmail  
EMAIL_1_SERVER=imap.gmail.com
EMAIL_1_ADDRESS=work@company.com
EMAIL_1_PASSWORD=apppassword2
```

### Mixed Gmail + Outlook
```env
# Primary: Gmail
IMAP_SERVER=imap.gmail.com
EMAIL_ADDRESS=hr@gmail.com
EMAIL_PASSWORD=gmailapppassword

# Secondary: Outlook
EMAIL_1_SERVER=outlook.office365.com
EMAIL_1_ADDRESS=recruitment@company.com
EMAIL_1_PASSWORD=outlookpassword
```

### Faster Scanning
```env
SCRAPER_INTERVAL_SECONDS=30  # Check every 30 seconds (default: 60)
SYNC_INTERVAL_MINUTES=5      # Sync every 5 minutes (default: 15)
```

---

## 📊 Expected Behavior

Once configured, the system will:

1. ✅ Connect to all configured email accounts
2. ✅ Scan monitored folders every 60 seconds
3. ✅ Extract candidate information from emails
4. ✅ Parse attached resumes (PDF, DOCX)
5. ✅ Store candidates in database
6. ✅ Avoid duplicates (checks email + name)
7. ✅ Use AI to extract skills and experience

**Log Output:**
```
📧 Loaded 3 email account(s)
✅ Connected to mohdmaahir786@gmail.com
✅ Connected to hr@outlook.com
✅ Connected to jobs@company.com
🔍 Scanning 5 folders: INBOX, Applications, Careers, Jobs, Resumes
📥 Found 23 new emails
🎯 Extracted 12 candidates
✅ Added 12 new candidates to database
```

---

## 🚀 Ready to Go!

Your system is configured for:
- ✅ Gmail (mohdmaahir786@gmail.com)
- ⏳ Add more accounts as needed
- ⏳ Enable auto-scraping in code

**Next Steps:**
1. Add additional email accounts (optional)
2. Uncomment auto-scraper in `main.py` 
3. Test by sending a resume to your configured email
4. Check dashboard for new candidates!
