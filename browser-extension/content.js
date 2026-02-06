// LinkedIn Profile Scraper - Content Script
// Runs on LinkedIn profile pages

(function() {
  'use strict';

  // Prevent multiple injections
  if (window.linkedInScraperInitialized) return;
  window.linkedInScraperInitialized = true;

  // Utility functions
  const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  
  const getTextContent = (selector, context = document) => {
    const el = context.querySelector(selector);
    return el ? el.textContent.trim() : '';
  };

  const getAllTextContent = (selector, context = document) => {
    const elements = context.querySelectorAll(selector);
    return Array.from(elements).map(el => el.textContent.trim()).filter(Boolean);
  };

  // Wait for element to appear
  const waitForElement = async (selector, timeout = 5000) => {
    const start = Date.now();
    while (Date.now() - start < timeout) {
      const el = document.querySelector(selector);
      if (el) return el;
      await wait(100);
    }
    return null;
  };

  // Main scraping function
  async function scrapeLinkedInProfile() {
    console.log('[AI Recruiter] Starting LinkedIn profile scrape...');
    
    // Wait for page to fully load
    await waitForElement('.pv-top-card');
    await wait(1000); // Extra wait for dynamic content

    const profile = {
      source: 'linkedin',
      scrapedAt: new Date().toISOString(),
      profileUrl: window.location.href,
      name: '',
      headline: '',
      location: '',
      email: '',
      phone: '',
      about: '',
      experience: [],
      education: [],
      skills: [],
      certifications: [],
      languages: [],
      connections: '',
      profileImage: ''
    };

    try {
      // Basic Info from top card
      profile.name = getTextContent('.pv-top-card h1') || 
                     getTextContent('[data-anonymize="person-name"]') ||
                     getTextContent('.text-heading-xlarge');
      
      profile.headline = getTextContent('.pv-top-card .text-body-medium') ||
                         getTextContent('[data-anonymize="headline"]') ||
                         getTextContent('.text-body-medium.break-words');
      
      profile.location = getTextContent('.pv-top-card .text-body-small.inline') ||
                         getTextContent('[data-anonymize="location"]') ||
                         getTextContent('.pv-text-details__left-panel .text-body-small:last-child');

      // Profile image
      const profileImg = document.querySelector('.pv-top-card-profile-picture__image') ||
                         document.querySelector('.presence-entity__image') ||
                         document.querySelector('img.pv-top-card-profile-picture__image--show');
      if (profileImg) {
        profile.profileImage = profileImg.src;
      }

      // Connections count
      const connectionsEl = document.querySelector('.pv-top-card--list-bullet li:last-child span') ||
                           document.querySelector('[href*="/connections/"]');
      if (connectionsEl) {
        profile.connections = connectionsEl.textContent.trim();
      }

      // About section
      const aboutSection = document.querySelector('#about') || 
                          document.querySelector('section.pv-about-section');
      if (aboutSection) {
        const aboutParent = aboutSection.closest('section') || aboutSection.parentElement?.parentElement;
        if (aboutParent) {
          const aboutText = aboutParent.querySelector('.pv-shared-text-with-see-more span[aria-hidden="true"]') ||
                           aboutParent.querySelector('.inline-show-more-text') ||
                           aboutParent.querySelector('.display-flex.full-width span[aria-hidden="true"]');
          if (aboutText) {
            profile.about = aboutText.textContent.trim();
          }
        }
      }

      // Experience section
      const experienceSection = document.querySelector('#experience') ||
                               document.querySelector('section.experience-section');
      if (experienceSection) {
        const expParent = experienceSection.closest('section') || experienceSection.parentElement?.parentElement;
        if (expParent) {
          const expItems = expParent.querySelectorAll('.pvs-list__paged-list-item, .pv-entity__position-group-pager li');
          
          expItems.forEach(item => {
            const exp = {
              title: '',
              company: '',
              duration: '',
              location: '',
              description: ''
            };

            // Try multiple selectors for job title
            exp.title = getTextContent('.mr1.t-bold span[aria-hidden="true"]', item) ||
                       getTextContent('.t-bold span[aria-hidden="true"]', item) ||
                       getTextContent('.pv-entity__summary-info h3', item) ||
                       getTextContent('[data-anonymize="job-title"]', item);

            // Company name
            exp.company = getTextContent('.t-14.t-normal span[aria-hidden="true"]', item) ||
                         getTextContent('.pv-entity__secondary-title', item) ||
                         getTextContent('[data-anonymize="company-name"]', item);

            // Duration
            const durationEl = item.querySelector('.t-14.t-normal.t-black--light span[aria-hidden="true"]') ||
                              item.querySelector('.pv-entity__date-range span:nth-child(2)');
            if (durationEl) {
              exp.duration = durationEl.textContent.trim();
            }

            // Location  
            const spans = item.querySelectorAll('.t-14.t-normal.t-black--light span[aria-hidden="true"]');
            if (spans.length > 1) {
              exp.location = spans[1].textContent.trim();
            }

            // Description
            exp.description = getTextContent('.pvs-list__outer-container .inline-show-more-text', item) ||
                             getTextContent('.pv-entity__description', item);

            if (exp.title || exp.company) {
              profile.experience.push(exp);
            }
          });
        }
      }

      // Education section
      const educationSection = document.querySelector('#education') ||
                              document.querySelector('section.education-section');
      if (educationSection) {
        const eduParent = educationSection.closest('section') || educationSection.parentElement?.parentElement;
        if (eduParent) {
          const eduItems = eduParent.querySelectorAll('.pvs-list__paged-list-item, .pv-entity__position-group-pager li');
          
          eduItems.forEach(item => {
            const edu = {
              school: '',
              degree: '',
              field: '',
              years: '',
              activities: ''
            };

            edu.school = getTextContent('.mr1.hoverable-link-text.t-bold span[aria-hidden="true"]', item) ||
                        getTextContent('.t-bold span[aria-hidden="true"]', item) ||
                        getTextContent('.pv-entity__school-name', item);

            edu.degree = getTextContent('.t-14.t-normal span[aria-hidden="true"]', item) ||
                        getTextContent('.pv-entity__degree-name span:nth-child(2)', item);

            const yearsEl = item.querySelector('.t-14.t-normal.t-black--light span[aria-hidden="true"]') ||
                           item.querySelector('.pv-entity__dates span:nth-child(2)');
            if (yearsEl) {
              edu.years = yearsEl.textContent.trim();
            }

            if (edu.school) {
              profile.education.push(edu);
            }
          });
        }
      }

      // Skills section
      const skillsSection = document.querySelector('#skills') ||
                           document.querySelector('section.skills-section');
      if (skillsSection) {
        const skillsParent = skillsSection.closest('section') || skillsSection.parentElement?.parentElement;
        if (skillsParent) {
          const skillItems = skillsParent.querySelectorAll('.pvs-list__paged-list-item .mr1.hoverable-link-text span[aria-hidden="true"]') ||
                            skillsParent.querySelectorAll('.pv-skill-category-entity__name-text');
          
          skillItems.forEach(skill => {
            const skillText = skill.textContent.trim();
            if (skillText && !profile.skills.includes(skillText)) {
              profile.skills.push(skillText);
            }
          });
        }
      }

      // Also try to get skills from featured/endorsements
      const endorsedSkills = document.querySelectorAll('[data-field="skill_card_skill_topic"] span[aria-hidden="true"]');
      endorsedSkills.forEach(skill => {
        const skillText = skill.textContent.trim();
        if (skillText && !profile.skills.includes(skillText)) {
          profile.skills.push(skillText);
        }
      });

      // Certifications
      const certsSection = document.querySelector('#licenses_and_certifications') ||
                          document.querySelector('section.certifications-section');
      if (certsSection) {
        const certsParent = certsSection.closest('section') || certsSection.parentElement?.parentElement;
        if (certsParent) {
          const certItems = certsParent.querySelectorAll('.pvs-list__paged-list-item');
          certItems.forEach(item => {
            const cert = {
              name: getTextContent('.t-bold span[aria-hidden="true"]', item),
              issuer: getTextContent('.t-14.t-normal span[aria-hidden="true"]', item),
              date: getTextContent('.t-14.t-normal.t-black--light span[aria-hidden="true"]', item)
            };
            if (cert.name) {
              profile.certifications.push(cert);
            }
          });
        }
      }

      // Languages
      const langSection = document.querySelector('#languages');
      if (langSection) {
        const langParent = langSection.closest('section') || langSection.parentElement?.parentElement;
        if (langParent) {
          const langItems = langParent.querySelectorAll('.pvs-list__paged-list-item');
          langItems.forEach(item => {
            const lang = {
              language: getTextContent('.t-bold span[aria-hidden="true"]', item),
              proficiency: getTextContent('.t-14.t-normal.t-black--light span[aria-hidden="true"]', item)
            };
            if (lang.language) {
              profile.languages.push(lang);
            }
          });
        }
      }

      // Try to get contact info (if available in contact info modal)
      // This might require clicking the "Contact info" link

      // Calculate experience years from experience entries
      profile.totalExperienceYears = calculateTotalExperience(profile.experience);

      console.log('[AI Recruiter] Scraped profile:', profile);
      return profile;

    } catch (error) {
      console.error('[AI Recruiter] Scraping error:', error);
      return { ...profile, error: error.message };
    }
  }

  // Calculate total years of experience
  function calculateTotalExperience(experiences) {
    let totalMonths = 0;
    
    experiences.forEach(exp => {
      if (exp.duration) {
        // Parse duration like "Jan 2020 - Present · 4 yrs 2 mos"
        const yearsMatch = exp.duration.match(/(\d+)\s*yr/);
        const monthsMatch = exp.duration.match(/(\d+)\s*mo/);
        
        if (yearsMatch) totalMonths += parseInt(yearsMatch[1]) * 12;
        if (monthsMatch) totalMonths += parseInt(monthsMatch[1]);
      }
    });

    return Math.round(totalMonths / 12 * 10) / 10; // Round to 1 decimal
  }

  // Create floating action button
  function createScraperButton() {
    // Remove existing button if any
    const existing = document.getElementById('ai-recruiter-scraper-btn');
    if (existing) existing.remove();

    const button = document.createElement('button');
    button.id = 'ai-recruiter-scraper-btn';
    button.innerHTML = `
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"></path>
        <circle cx="9" cy="7" r="4"></circle>
        <path d="M22 21v-2a4 4 0 0 0-3-3.87"></path>
        <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
      </svg>
      <span>Add to AI Recruiter</span>
    `;
    button.title = 'Scrape this profile and add to AI Recruiter';
    
    button.addEventListener('click', async () => {
      button.disabled = true;
      button.innerHTML = `
        <svg class="animate-spin" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10" stroke-dasharray="60" stroke-dashoffset="20"></circle>
        </svg>
        <span>Scraping...</span>
      `;

      try {
        const profile = await scrapeLinkedInProfile();
        
        // Send to background script
        chrome.runtime.sendMessage({
          action: 'profileScraped',
          data: profile
        }, (response) => {
          if (response && response.success) {
            button.innerHTML = `
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
              <span>Added!</span>
            `;
            button.classList.add('success');
            showNotification('Profile added to AI Recruiter!', 'success');
          } else {
            throw new Error(response?.error || 'Failed to save profile');
          }
        });
      } catch (error) {
        console.error('[AI Recruiter] Error:', error);
        button.innerHTML = `
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="15" y1="9" x2="9" y2="15"></line>
            <line x1="9" y1="9" x2="15" y2="15"></line>
          </svg>
          <span>Error</span>
        `;
        button.classList.add('error');
        showNotification('Failed to scrape profile. Please try again.', 'error');
      }

      // Reset button after 3 seconds
      setTimeout(() => {
        button.disabled = false;
        button.classList.remove('success', 'error');
        button.innerHTML = `
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"></path>
            <circle cx="9" cy="7" r="4"></circle>
            <path d="M22 21v-2a4 4 0 0 0-3-3.87"></path>
            <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
          </svg>
          <span>Add to AI Recruiter</span>
        `;
      }, 3000);
    });

    document.body.appendChild(button);
  }

  // Show notification toast
  function showNotification(message, type = 'info') {
    const existing = document.getElementById('ai-recruiter-notification');
    if (existing) existing.remove();

    const notification = document.createElement('div');
    notification.id = 'ai-recruiter-notification';
    notification.className = `ai-recruiter-notification ${type}`;
    notification.innerHTML = `
      <span>${message}</span>
      <button onclick="this.parentElement.remove()">×</button>
    `;
    
    document.body.appendChild(notification);

    // Auto-remove after 5 seconds
    setTimeout(() => notification.remove(), 5000);
  }

  // Listen for messages from popup/background
  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === 'scrapeProfile') {
      scrapeLinkedInProfile().then(profile => {
        sendResponse({ success: true, data: profile });
      }).catch(error => {
        sendResponse({ success: false, error: error.message });
      });
      return true; // Keep channel open for async response
    }
    
    if (request.action === 'ping') {
      sendResponse({ status: 'ready' });
    }
  });

  // Initialize
  function init() {
    // Only run on profile pages
    if (window.location.pathname.startsWith('/in/')) {
      console.log('[AI Recruiter] LinkedIn profile detected, initializing scraper...');
      
      // Wait for page to settle then add button
      setTimeout(createScraperButton, 2000);

      // Re-add button on SPA navigation
      const observer = new MutationObserver((mutations) => {
        if (!document.getElementById('ai-recruiter-scraper-btn')) {
          createScraperButton();
        }
      });

      observer.observe(document.body, { childList: true, subtree: true });
    }
  }

  // Run on page load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
