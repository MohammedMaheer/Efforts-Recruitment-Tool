// AI Recruiter LinkedIn Scraper - Background Service Worker

const API_BASE_URL = 'http://localhost:8000';

// Store scraped profiles temporarily
let scrapedProfiles = [];

// Handle messages from content script and popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  console.log('[AI Recruiter BG] Received message:', request.action);

  switch (request.action) {
    case 'profileScraped':
      handleProfileScraped(request.data)
        .then(result => sendResponse(result))
        .catch(error => sendResponse({ success: false, error: error.message }));
      return true; // Keep channel open for async

    case 'getScrapedProfiles':
      sendResponse({ profiles: scrapedProfiles });
      break;

    case 'clearProfiles':
      scrapedProfiles = [];
      sendResponse({ success: true });
      break;

    case 'sendToBackend':
      sendProfilesToBackend(request.profiles)
        .then(result => sendResponse(result))
        .catch(error => sendResponse({ success: false, error: error.message }));
      return true;

    case 'getApiStatus':
      checkApiStatus()
        .then(status => sendResponse(status))
        .catch(error => sendResponse({ connected: false, error: error.message }));
      return true;

    case 'updateSettings':
      chrome.storage.sync.set(request.settings, () => {
        sendResponse({ success: true });
      });
      return true;

    case 'getSettings':
      chrome.storage.sync.get(['apiUrl', 'autoSend', 'jobCategory'], (settings) => {
        sendResponse(settings);
      });
      return true;

    default:
      sendResponse({ error: 'Unknown action' });
  }
});

// Handle scraped profile
async function handleProfileScraped(profileData) {
  console.log('[AI Recruiter BG] Processing scraped profile:', profileData.name);

  // Add to local store
  const existingIndex = scrapedProfiles.findIndex(p => p.profileUrl === profileData.profileUrl);
  if (existingIndex >= 0) {
    scrapedProfiles[existingIndex] = profileData;
  } else {
    scrapedProfiles.unshift(profileData);
  }

  // Keep only last 50 profiles
  if (scrapedProfiles.length > 50) {
    scrapedProfiles = scrapedProfiles.slice(0, 50);
  }

  // Save to storage
  await chrome.storage.local.set({ scrapedProfiles });

  // Check if auto-send is enabled
  const settings = await chrome.storage.sync.get(['autoSend', 'apiUrl']);
  
  if (settings.autoSend) {
    try {
      await sendProfilesToBackend([profileData]);
      return { success: true, message: 'Profile saved and sent to backend' };
    } catch (error) {
      console.error('[AI Recruiter BG] Auto-send failed:', error);
      return { success: true, message: 'Profile saved locally (backend unavailable)', warning: true };
    }
  }

  return { success: true, message: 'Profile saved locally' };
}

// Send profiles to backend API
async function sendProfilesToBackend(profiles) {
  const settings = await chrome.storage.sync.get(['apiUrl', 'jobCategory']);
  const apiUrl = settings.apiUrl || API_BASE_URL;

  const results = [];

  for (const profile of profiles) {
    try {
      // Transform LinkedIn profile to candidate format
      const candidateData = transformToCandidateFormat(profile, settings.jobCategory);

      const response = await fetch(`${apiUrl}/api/candidates/linkedin`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(candidateData)
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `HTTP ${response.status}`);
      }

      const result = await response.json();
      results.push({ profile: profile.name, success: true, candidateId: result.id });

      // Update badge
      chrome.action.setBadgeText({ text: '✓' });
      chrome.action.setBadgeBackgroundColor({ color: '#10b981' });
      setTimeout(() => chrome.action.setBadgeText({ text: '' }), 3000);

    } catch (error) {
      console.error('[AI Recruiter BG] Failed to send profile:', error);
      results.push({ profile: profile.name, success: false, error: error.message });
    }
  }

  return { results };
}

// Transform LinkedIn profile to backend candidate format
function transformToCandidateFormat(profile, jobCategory = 'General') {
  // Extract email from profile if available, otherwise mark as LinkedIn import
  const email = profile.email || `linkedin_${profile.profileUrl.split('/in/')[1]?.replace(/\//g, '')}@import.linkedin`;
  
  // Build resume text from profile sections
  let resumeText = `${profile.name}\n${profile.headline}\n${profile.location}\n\n`;
  
  if (profile.about) {
    resumeText += `ABOUT\n${profile.about}\n\n`;
  }

  if (profile.experience?.length > 0) {
    resumeText += 'EXPERIENCE\n';
    profile.experience.forEach(exp => {
      resumeText += `${exp.title} at ${exp.company}\n`;
      resumeText += `${exp.duration} | ${exp.location}\n`;
      if (exp.description) resumeText += `${exp.description}\n`;
      resumeText += '\n';
    });
  }

  if (profile.education?.length > 0) {
    resumeText += 'EDUCATION\n';
    profile.education.forEach(edu => {
      resumeText += `${edu.school}\n`;
      resumeText += `${edu.degree} ${edu.field ? '- ' + edu.field : ''}\n`;
      if (edu.years) resumeText += `${edu.years}\n`;
      resumeText += '\n';
    });
  }

  if (profile.skills?.length > 0) {
    resumeText += `SKILLS\n${profile.skills.join(', ')}\n\n`;
  }

  if (profile.certifications?.length > 0) {
    resumeText += 'CERTIFICATIONS\n';
    profile.certifications.forEach(cert => {
      resumeText += `${cert.name} - ${cert.issuer}\n`;
    });
    resumeText += '\n';
  }

  return {
    name: profile.name,
    email: email,
    phone: profile.phone || '',
    location: profile.location || '',
    linkedin: profile.profileUrl,
    source: 'linkedin_extension',
    job_category: jobCategory,
    skills: profile.skills || [],
    experience: profile.totalExperienceYears || 0,
    resume_text: resumeText,
    profile_image: profile.profileImage || '',
    headline: profile.headline || '',
    education: profile.education || [],
    work_experience: profile.experience || [],
    certifications: profile.certifications || [],
    languages: profile.languages || [],
    scraped_at: profile.scrapedAt
  };
}

// Check backend API status
async function checkApiStatus() {
  const settings = await chrome.storage.sync.get(['apiUrl']);
  const apiUrl = settings.apiUrl || API_BASE_URL;

  try {
    const response = await fetch(`${apiUrl}/health`, {
      method: 'GET',
      headers: { 'Accept': 'application/json' }
    });

    if (response.ok) {
      return { connected: true, url: apiUrl };
    } else {
      return { connected: false, error: `HTTP ${response.status}` };
    }
  } catch (error) {
    return { connected: false, error: error.message };
  }
}

// Initialize extension
chrome.runtime.onInstalled.addListener(() => {
  console.log('[AI Recruiter] Extension installed');
  
  // Set default settings
  chrome.storage.sync.set({
    apiUrl: API_BASE_URL,
    autoSend: false,
    jobCategory: 'General'
  });

  // Load any previously scraped profiles
  chrome.storage.local.get(['scrapedProfiles'], (result) => {
    if (result.scrapedProfiles) {
      scrapedProfiles = result.scrapedProfiles;
    }
  });
});

// Restore profiles on startup
chrome.runtime.onStartup.addListener(() => {
  chrome.storage.local.get(['scrapedProfiles'], (result) => {
    if (result.scrapedProfiles) {
      scrapedProfiles = result.scrapedProfiles;
    }
  });
});
