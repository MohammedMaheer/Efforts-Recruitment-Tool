// AI Recruiter LinkedIn Scraper - Popup Script

document.addEventListener('DOMContentLoaded', init);

let scrapedProfiles = [];
let currentTab = null;

async function init() {
  // Initialize UI
  setupTabs();
  await checkConnection();
  await loadSettings();
  await loadProfiles();
  await checkCurrentTab();

  // Setup event listeners
  document.getElementById('scrapeBtn').addEventListener('click', scrapeCurrentProfile);
  document.getElementById('clearBtn').addEventListener('click', clearProfiles);
  document.getElementById('sendAllBtn').addEventListener('click', sendAllProfiles);
  document.getElementById('testConnection').addEventListener('click', testConnection);
  document.getElementById('saveSettings').addEventListener('click', saveSettings);
}

// Tab Navigation
function setupTabs() {
  const tabs = document.querySelectorAll('.tab');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const tabName = tab.dataset.tab;
      
      // Update tabs
      tabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      
      // Update content
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      document.getElementById(`${tabName}-tab`).classList.add('active');
    });
  });
}

// Check API Connection
async function checkConnection() {
  const statusEl = document.getElementById('connectionStatus');
  
  try {
    const response = await chrome.runtime.sendMessage({ action: 'getApiStatus' });
    
    if (response.connected) {
      statusEl.classList.add('connected');
      statusEl.classList.remove('disconnected');
      statusEl.querySelector('.status-text').textContent = 'Connected';
    } else {
      statusEl.classList.add('disconnected');
      statusEl.classList.remove('connected');
      statusEl.querySelector('.status-text').textContent = 'Offline';
    }
  } catch (error) {
    statusEl.classList.add('disconnected');
    statusEl.querySelector('.status-text').textContent = 'Error';
  }
}

// Load Settings
async function loadSettings() {
  const response = await chrome.runtime.sendMessage({ action: 'getSettings' });
  
  if (response.apiUrl) {
    document.getElementById('apiUrl').value = response.apiUrl;
  }
  if (response.autoSend !== undefined) {
    document.getElementById('autoSend').checked = response.autoSend;
  }
  if (response.jobCategory) {
    document.getElementById('jobCategory').value = response.jobCategory;
  }
}

// Save Settings
async function saveSettings() {
  const settings = {
    apiUrl: document.getElementById('apiUrl').value || 'http://localhost:8000',
    autoSend: document.getElementById('autoSend').checked,
    jobCategory: document.getElementById('jobCategory').value
  };

  await chrome.runtime.sendMessage({ action: 'updateSettings', settings });
  
  // Show feedback
  const btn = document.getElementById('saveSettings');
  const originalText = btn.textContent;
  btn.textContent = 'Saved!';
  btn.disabled = true;
  
  setTimeout(() => {
    btn.textContent = originalText;
    btn.disabled = false;
  }, 2000);

  // Re-check connection with new URL
  await checkConnection();
}

// Test Connection
async function testConnection() {
  const btn = document.getElementById('testConnection');
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner">⟳</span> Testing...`;

  // Save current URL first
  const apiUrl = document.getElementById('apiUrl').value;
  await chrome.runtime.sendMessage({ 
    action: 'updateSettings', 
    settings: { apiUrl } 
  });

  await checkConnection();
  
  btn.disabled = false;
  btn.textContent = 'Test Connection';
}

// Check Current Tab
async function checkCurrentTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentTab = tab;

  const currentPageEl = document.getElementById('currentPage');
  const scrapeBtn = document.getElementById('scrapeBtn');

  if (tab.url && tab.url.includes('linkedin.com/in/')) {
    // We're on a LinkedIn profile page
    scrapeBtn.disabled = false;
    
    // Try to get profile preview from content script
    try {
      const response = await chrome.tabs.sendMessage(tab.id, { action: 'ping' });
      if (response && response.status === 'ready') {
        currentPageEl.innerHTML = `
          <div class="current-page-avatar" style="background: var(--primary); display: flex; align-items: center; justify-content: center; color: white; font-weight: 600;">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z"></path>
              <rect x="2" y="9" width="4" height="12"></rect>
              <circle cx="4" cy="4" r="2"></circle>
            </svg>
          </div>
          <div class="current-page-info">
            <div class="current-page-name">LinkedIn Profile Detected</div>
            <div class="current-page-headline">Click "Scrape" to extract profile data</div>
          </div>
        `;
      }
    } catch (error) {
      // Content script might not be injected yet
      currentPageEl.innerHTML = `
        <p class="muted">Refresh the LinkedIn page to enable scraping</p>
      `;
      scrapeBtn.disabled = true;
    }
  } else {
    currentPageEl.innerHTML = `
      <p class="muted">Navigate to a LinkedIn profile page (linkedin.com/in/...)</p>
    `;
    scrapeBtn.disabled = true;
  }
}

// Load Scraped Profiles
async function loadProfiles() {
  const response = await chrome.runtime.sendMessage({ action: 'getScrapedProfiles' });
  scrapedProfiles = response.profiles || [];
  
  updateProfilesList();
  updateProfileCount();
}

// Update Profiles List UI
function updateProfilesList() {
  const listEl = document.getElementById('profilesList');
  const sendAllBtn = document.getElementById('sendAllBtn');

  if (scrapedProfiles.length === 0) {
    listEl.innerHTML = '<p class="muted">No profiles scraped yet</p>';
    sendAllBtn.disabled = true;
    return;
  }

  sendAllBtn.disabled = false;

  listEl.innerHTML = scrapedProfiles.map((profile, index) => `
    <div class="profile-item" data-index="${index}">
      <img 
        class="profile-item-avatar" 
        src="${profile.profileImage || 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#9CA3AF"><circle cx="12" cy="8" r="4"/><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/></svg>')}"
        alt="${profile.name}"
        onerror="this.src='data:image/svg+xml,' + encodeURIComponent('<svg xmlns=\\'http://www.w3.org/2000/svg\\' viewBox=\\'0 0 24 24\\' fill=\\'#9CA3AF\\'><circle cx=\\'12\\' cy=\\'8\\' r=\\'4\\'/><path d=\\'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2\\'/></svg>')"
      />
      <div class="profile-item-info">
        <div class="profile-item-name">${profile.name}</div>
        <div class="profile-item-meta">
          <span>${profile.skills?.length || 0} skills</span>
          <span>•</span>
          <span>${profile.totalExperienceYears || 0} yrs exp</span>
        </div>
        ${profile.skills?.length > 0 ? `
          <div class="profile-item-skills">
            ${profile.skills.slice(0, 3).map(s => `<span class="skill-tag">${s}</span>`).join('')}
            ${profile.skills.length > 3 ? `<span class="skill-tag">+${profile.skills.length - 3}</span>` : ''}
          </div>
        ` : ''}
      </div>
      <div class="profile-item-actions">
        <button class="view-btn" title="View on LinkedIn" data-url="${profile.profileUrl}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
            <polyline points="15 3 21 3 21 9"></polyline>
            <line x1="10" y1="14" x2="21" y2="3"></line>
          </svg>
        </button>
        <button class="remove-btn" title="Remove" data-index="${index}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>
    </div>
  `).join('');

  // Add event listeners
  listEl.querySelectorAll('.view-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      chrome.tabs.create({ url: btn.dataset.url });
    });
  });

  listEl.querySelectorAll('.remove-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      removeProfile(parseInt(btn.dataset.index));
    });
  });
}

// Update Profile Count Badge
function updateProfileCount() {
  document.getElementById('profileCount').textContent = scrapedProfiles.length;
}

// Scrape Current Profile
async function scrapeCurrentProfile() {
  const btn = document.getElementById('scrapeBtn');
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner">⟳</span> Scraping...`;

  try {
    const response = await chrome.tabs.sendMessage(currentTab.id, { action: 'scrapeProfile' });
    
    if (response.success) {
      // Send to background to store
      await chrome.runtime.sendMessage({
        action: 'profileScraped',
        data: response.data
      });

      // Reload profiles
      await loadProfiles();
      
      btn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
        Scraped!
      `;
    } else {
      throw new Error(response.error);
    }
  } catch (error) {
    console.error('Scrape error:', error);
    btn.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10"></circle>
        <line x1="15" y1="9" x2="9" y2="15"></line>
        <line x1="9" y1="9" x2="15" y2="15"></line>
      </svg>
      Error
    `;
  }

  setTimeout(() => {
    btn.disabled = false;
    btn.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
        <polyline points="7 10 12 15 17 10"></polyline>
        <line x1="12" y1="15" x2="12" y2="3"></line>
      </svg>
      Scrape This Profile
    `;
  }, 2000);
}

// Remove Profile
async function removeProfile(index) {
  scrapedProfiles.splice(index, 1);
  await chrome.storage.local.set({ scrapedProfiles });
  updateProfilesList();
  updateProfileCount();
}

// Clear All Profiles
async function clearProfiles() {
  if (confirm('Are you sure you want to clear all scraped profiles?')) {
    scrapedProfiles = [];
    await chrome.runtime.sendMessage({ action: 'clearProfiles' });
    updateProfilesList();
    updateProfileCount();
  }
}

// Send All Profiles to Backend
async function sendAllProfiles() {
  const btn = document.getElementById('sendAllBtn');
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner">⟳</span> Sending...`;

  try {
    const response = await chrome.runtime.sendMessage({
      action: 'sendToBackend',
      profiles: scrapedProfiles
    });

    if (response.results) {
      const successful = response.results.filter(r => r.success).length;
      const failed = response.results.filter(r => !r.success).length;

      btn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
        ${successful} sent${failed > 0 ? `, ${failed} failed` : ''}
      `;
    }
  } catch (error) {
    console.error('Send error:', error);
    btn.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10"></circle>
        <line x1="15" y1="9" x2="9" y2="15"></line>
        <line x1="9" y1="9" x2="15" y2="15"></line>
      </svg>
      Failed
    `;
  }

  setTimeout(() => {
    btn.disabled = false;
    btn.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <line x1="22" y1="2" x2="11" y2="13"></line>
        <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
      </svg>
      Send All to AI Recruiter
    `;
  }, 3000);
}
