# AI Recruiter - LinkedIn Profile Scraper Extension

A Chrome/Edge browser extension that scrapes LinkedIn profiles and imports them directly into the AI Recruiter platform.

## Features

- 🔍 **One-Click Scraping** - Scrape any LinkedIn profile with a single click
- 📊 **Rich Data Extraction** - Extracts name, headline, experience, education, skills, certifications, and more
- 🤖 **AI Analysis** - Automatically analyzes and scores candidates using AI
- 📁 **Batch Import** - Scrape multiple profiles and send them all at once
- ⚡ **Auto-Send** - Optionally auto-send profiles to backend immediately
- 🔗 **Seamless Integration** - Direct integration with AI Recruiter API

## Installation

### For Development (Unpacked Extension)

1. Open Chrome/Edge and navigate to `chrome://extensions/` or `edge://extensions/`
2. Enable **Developer mode** (toggle in top-right corner)
3. Click **Load unpacked**
4. Select the `browser-extension` folder from this project
5. The extension icon should appear in your toolbar

### Creating Icons

Before loading the extension, you'll need to add icon files. Create PNG images in the `icons/` folder:
- `icon16.png` - 16x16 pixels
- `icon32.png` - 32x32 pixels  
- `icon48.png` - 48x48 pixels
- `icon128.png` - 128x128 pixels

You can use any icon generator or create simple icons with the AI Recruiter logo.

## Usage

### Scraping a Profile

1. Navigate to any LinkedIn profile page (e.g., `linkedin.com/in/username`)
2. Click the floating **"Add to AI Recruiter"** button that appears in the bottom-right corner
3. Wait for the scraping to complete
4. The profile will be saved locally and optionally sent to your backend

### Using the Popup

1. Click the extension icon in your browser toolbar
2. **Profiles Tab**:
   - View the current LinkedIn page status
   - See all scraped profiles
   - Send individual or all profiles to AI Recruiter
3. **Settings Tab**:
   - Configure your backend API URL
   - Enable/disable auto-send
   - Set default job category for imports

## Configuration

### Backend URL
By default, the extension connects to `http://localhost:8000`. Change this in Settings if your backend runs on a different URL.

### Auto-Send
When enabled, scraped profiles are automatically sent to the backend. Disable this to review profiles before sending.

### Job Category
Set the default job category for imported candidates. This helps with initial categorization.

## Data Extracted

The extension extracts the following from LinkedIn profiles:

| Field | Description |
|-------|-------------|
| Name | Full name from profile |
| Headline | Professional headline |
| Location | Geographic location |
| About | Summary/bio section |
| Experience | All work history with titles, companies, dates |
| Education | Schools, degrees, fields of study |
| Skills | All listed skills |
| Certifications | Licenses and certifications |
| Languages | Language proficiencies |
| Profile Image | Profile photo URL |
| Total Experience | Calculated years of experience |

## API Endpoint

The extension sends data to:

```
POST /api/candidates/linkedin
```

Request body:
```json
{
  "name": "John Doe",
  "email": "john.doe@example.com",
  "phone": "",
  "location": "San Francisco, CA",
  "linkedin": "https://linkedin.com/in/johndoe",
  "source": "linkedin_extension",
  "job_category": "Engineering",
  "skills": ["Python", "React", "AWS"],
  "experience": 5.5,
  "resume_text": "Full profile text...",
  "profile_image": "https://...",
  "headline": "Senior Software Engineer",
  "education": [...],
  "work_experience": [...],
  "certifications": [...],
  "languages": [...]
}
```

## Troubleshooting

### Extension Not Loading
- Ensure Developer mode is enabled
- Check for errors in `chrome://extensions/`
- Make sure all files are present

### Scraping Not Working
- Refresh the LinkedIn page after installing the extension
- Check if content script loaded (look for floating button)
- LinkedIn may have updated their DOM - check console for errors

### Connection Failed
- Verify backend is running on the configured URL
- Check CORS settings allow the extension origin
- Test the `/health` endpoint manually

### Profile Not Saved
- Check browser console for errors
- Verify API endpoint returns 200
- Check backend logs for errors

## Privacy & Permissions

The extension requires:
- **activeTab** - To access the current LinkedIn page
- **storage** - To save settings and scraped profiles locally
- **tabs** - To detect LinkedIn profile pages
- **host_permissions** - To connect to LinkedIn and your backend

No data is sent to third parties. All data goes only to your configured backend.

## Development

### Files Structure
```
browser-extension/
├── manifest.json      # Extension manifest
├── content.js         # Injected into LinkedIn pages
├── content.css        # Styles for injected elements
├── background.js      # Service worker for API calls
├── popup.html         # Extension popup UI
├── popup.css          # Popup styles
├── popup.js           # Popup logic
└── icons/            # Extension icons
```

### Testing Changes
1. Make changes to source files
2. Go to `chrome://extensions/`
3. Click the refresh icon on the extension card
4. Reload the LinkedIn page

## License

Part of AI Recruiter Platform. For internal use.
