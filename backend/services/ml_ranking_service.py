"""
ML-Based Resume Ranking Service
Learns from hiring decisions to rank future candidates
Uses scikit-learn for training and inference
"""
import json
import logging
import os
import pickle
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

# Model storage path
MODEL_PATH = Path(__file__).parent.parent / "models_trained"
MODEL_PATH.mkdir(exist_ok=True)


class ResumeRankingModel:
    """
    Custom ML model that learns from hiring decisions
    Features: skills match, experience, education, response time, etc.
    Target: hired (1) vs not hired (0)
    """
    
    def __init__(self):
        self.model = None
        self.feature_names = [
            'skill_count',
            'skill_match_ratio',
            'years_experience',
            'education_level',  # 0=none, 1=bachelors, 2=masters, 3=phd
            'has_relevant_degree',
            'job_hopping_score',  # Lower is better
            'employment_gap_months',
            'resume_quality_score',
            'response_time_hours',  # Time to respond to outreach
            'profile_completeness',
            'linkedin_present',
            'phone_present',
            'location_match',
            'salary_in_range',
            'keyword_density',
            'achievement_count',  # Quantified achievements
            'certification_count',
            'project_count',
            'referral_score',  # 0=no referral, 1=weak, 2=strong
            'previous_company_tier',  # 0=unknown, 1=startup, 2=mid, 3=enterprise
        ]
        self.scaler = None
        self.training_history = []
        self._load_model()
    
    def _load_model(self):
        """Load trained model from disk if exists"""
        model_file = MODEL_PATH / "ranking_model.pkl"
        scaler_file = MODEL_PATH / "ranking_scaler.pkl"
        
        if model_file.exists() and scaler_file.exists():
            try:
                with open(model_file, 'rb') as f:
                    self.model = pickle.load(f)
                with open(scaler_file, 'rb') as f:
                    self.scaler = pickle.load(f)
                logger.info("✅ Loaded trained ranking model")
            except Exception as e:
                logger.warning(f"Could not load model: {e}")
                self._initialize_default_model()
        else:
            self._initialize_default_model()
    
    def _initialize_default_model(self):
        """Initialize with default weights based on recruiting best practices"""
        try:
            from sklearn.ensemble import GradientBoostingClassifier
            from sklearn.preprocessing import StandardScaler
            
            self.model = GradientBoostingClassifier(
                n_estimators=100,
                learning_rate=0.1,
                max_depth=5,
                random_state=42
            )
            self.scaler = StandardScaler()
            
            # Generate synthetic training data based on recruiting best practices
            # This gives the model a reasonable starting point
            np.random.seed(42)
            n_samples = 500
            
            # Generate features
            X = np.random.rand(n_samples, len(self.feature_names))
            
            # Scale features to realistic ranges
            X[:, 0] *= 20  # skill_count (0-20)
            X[:, 1] = X[:, 1]  # skill_match_ratio (0-1)
            X[:, 2] *= 15  # years_experience (0-15)
            X[:, 3] = np.random.randint(0, 4, n_samples)  # education_level
            X[:, 4] = np.random.randint(0, 2, n_samples)  # has_relevant_degree
            X[:, 5] *= 5  # job_hopping_score (0-5)
            X[:, 6] *= 24  # employment_gap_months (0-24)
            X[:, 7] *= 100  # resume_quality_score (0-100)
            X[:, 8] *= 72  # response_time_hours (0-72)
            X[:, 9] = X[:, 9]  # profile_completeness (0-1)
            X[:, 10] = np.random.randint(0, 2, n_samples)  # linkedin_present
            X[:, 11] = np.random.randint(0, 2, n_samples)  # phone_present
            X[:, 12] = np.random.randint(0, 2, n_samples)  # location_match
            X[:, 13] = np.random.randint(0, 2, n_samples)  # salary_in_range
            X[:, 14] *= 0.1  # keyword_density (0-0.1)
            X[:, 15] *= 10  # achievement_count (0-10)
            X[:, 16] *= 5  # certification_count (0-5)
            X[:, 17] *= 10  # project_count (0-10)
            X[:, 18] = np.random.randint(0, 3, n_samples)  # referral_score
            X[:, 19] = np.random.randint(0, 4, n_samples)  # previous_company_tier
            
            # Generate labels based on weighted scoring (simulating hiring decisions)
            scores = (
                X[:, 1] * 25 +  # skill_match_ratio (most important)
                np.minimum(X[:, 2], 10) * 2 +  # years_experience (diminishing returns)
                X[:, 3] * 5 +  # education_level
                X[:, 4] * 10 +  # has_relevant_degree
                (5 - X[:, 5]) * 3 +  # job_hopping (lower is better)
                (24 - X[:, 6]) * 0.5 +  # employment_gap (lower is better)
                X[:, 7] * 0.2 +  # resume_quality
                (72 - X[:, 8]) * 0.1 +  # response_time (faster is better)
                X[:, 9] * 10 +  # profile_completeness
                X[:, 10] * 3 +  # linkedin
                X[:, 12] * 5 +  # location_match
                X[:, 15] * 2 +  # achievement_count
                X[:, 16] * 3 +  # certifications
                X[:, 18] * 10 +  # referral_score
                X[:, 19] * 3  # company_tier
            )
            
            # Convert to binary labels (top 30% get hired)
            threshold = np.percentile(scores, 70)
            y = (scores >= threshold).astype(int)
            
            # Fit scaler and model
            X_scaled = self.scaler.fit_transform(X)
            self.model.fit(X_scaled, y)
            
            # Save the initialized model
            self._save_model()
            
            logger.info("✅ Initialized ranking model with best-practice weights")
            
        except ImportError:
            logger.warning("scikit-learn not installed, using simple scoring")
            self.model = None
            self.scaler = None
    
    def _save_model(self):
        """Save model to disk"""
        if self.model is not None and self.scaler is not None:
            try:
                with open(MODEL_PATH / "ranking_model.pkl", 'wb') as f:
                    pickle.dump(self.model, f)
                with open(MODEL_PATH / "ranking_scaler.pkl", 'wb') as f:
                    pickle.dump(self.scaler, f)
                logger.info("💾 Saved ranking model")
            except Exception as e:
                logger.error(f"Could not save model: {e}")
    
    def extract_features(self, candidate: Dict, job_requirements: Dict = None) -> np.ndarray:
        """Extract ML features from a candidate profile"""
        job_requirements = job_requirements or {}
        required_skills = set(s.lower() for s in job_requirements.get('skills', []))
        candidate_skills = set(s.lower() for s in candidate.get('skills', []))
        
        # Calculate skill match ratio
        if required_skills:
            skill_match = len(candidate_skills & required_skills) / len(required_skills)
        else:
            skill_match = 0.5  # Default if no requirements
        
        # Education level mapping
        education = candidate.get('education', [])
        education_level = 0
        has_relevant = 0
        
        if isinstance(education, list):
            for edu in education:
                if isinstance(edu, dict):
                    degree = edu.get('degree', '').lower()
                    field = edu.get('field', '').lower()
                elif isinstance(edu, str):
                    degree = edu.lower()
                    field = ''
                else:
                    continue
                
                if 'phd' in degree or 'doctorate' in degree:
                    education_level = max(education_level, 3)
                elif 'master' in degree or 'mba' in degree or 'ms ' in degree:
                    education_level = max(education_level, 2)
                elif 'bachelor' in degree or 'bs ' in degree or 'ba ' in degree:
                    education_level = max(education_level, 1)
                
                # Check relevant degree
                job_category = job_requirements.get('category', '').lower()
                if any(kw in field for kw in ['computer', 'software', 'engineering', 'data', 'business']):
                    if job_category in ['software', 'data', 'engineering', 'business']:
                        has_relevant = 1
        
        # Work history analysis
        work_history = candidate.get('workHistory', []) or []
        job_hopping_score = 0
        gap_months = 0
        achievement_count = 0
        company_tier = 0
        
        if isinstance(work_history, list) and len(work_history) > 0:
            # Job hopping: > 3 jobs in 5 years is concerning
            recent_jobs = len([j for j in work_history if isinstance(j, dict)])
            job_hopping_score = max(0, min(5, recent_jobs - 2))
            
            # Count achievements (bullet points with numbers)
            for job in work_history:
                if isinstance(job, dict):
                    desc = job.get('description', '')
                    if desc:
                        # Count quantified achievements
                        import re
                        numbers = re.findall(r'\d+[%$kKmM]|\$\d+', desc)
                        achievement_count += len(numbers)
                    
                    # Company tier estimation
                    company = job.get('company', '').lower()
                    big_tech = ['google', 'microsoft', 'amazon', 'meta', 'apple', 'netflix']
                    enterprise = ['ibm', 'oracle', 'salesforce', 'sap', 'accenture']
                    
                    if any(c in company for c in big_tech):
                        company_tier = max(company_tier, 3)
                    elif any(c in company for c in enterprise):
                        company_tier = max(company_tier, 2)
        
        # Build feature vector
        features = np.array([
            len(candidate_skills),  # skill_count
            skill_match,  # skill_match_ratio
            candidate.get('experience', 0),  # years_experience
            education_level,
            has_relevant,
            job_hopping_score,
            gap_months,
            candidate.get('matchScore', 50),  # resume_quality_score
            24,  # response_time_hours (default - updated when they respond)
            self._calculate_completeness(candidate),
            1 if candidate.get('linkedin') else 0,
            1 if candidate.get('phone') else 0,
            self._check_location_match(candidate, job_requirements),
            1,  # salary_in_range (default - needs salary info)
            0.05,  # keyword_density (placeholder)
            achievement_count,
            0,  # certification_count (needs parsing)
            0,  # project_count (needs parsing)
            0,  # referral_score
            company_tier,
        ])
        
        return features.reshape(1, -1)
    
    def _calculate_completeness(self, candidate: Dict) -> float:
        """Calculate profile completeness (0-1)"""
        fields = ['name', 'email', 'phone', 'location', 'skills', 'experience', 
                  'education', 'summary', 'linkedin', 'workHistory']
        present = sum(1 for f in fields if candidate.get(f))
        return present / len(fields)
    
    def _check_location_match(self, candidate: Dict, job_req: Dict) -> int:
        """Check if candidate location matches job location"""
        if not job_req.get('location'):
            return 1  # No location requirement
        
        candidate_loc = (candidate.get('location') or '').lower()
        job_loc = job_req.get('location', '').lower()
        
        if not candidate_loc:
            return 0
        
        # Simple matching - could be enhanced with geocoding
        if job_loc in candidate_loc or candidate_loc in job_loc:
            return 1
        
        # Check for remote
        if 'remote' in job_loc or 'remote' in candidate_loc:
            return 1
        
        return 0
    
    def predict_hire_probability(self, candidate: Dict, job_requirements: Dict = None) -> float:
        """
        Predict probability that this candidate will be hired
        Returns: 0-100 score
        """
        if self.model is None or self.scaler is None:
            # Fallback to simple scoring
            return candidate.get('matchScore', 50)
        
        try:
            features = self.extract_features(candidate, job_requirements)
            features_scaled = self.scaler.transform(features)
            
            # Get probability of positive class (hired)
            proba = self.model.predict_proba(features_scaled)[0][1]
            return round(proba * 100, 1)
        except Exception as e:
            logger.warning(f"Prediction error: {e}")
            return candidate.get('matchScore', 50)
    
    def rank_candidates(self, candidates: List[Dict], job_requirements: Dict = None) -> List[Dict]:
        """
        Rank a list of candidates by hire probability
        Returns candidates sorted by ML score (highest first)
        """
        for candidate in candidates:
            candidate['ml_rank_score'] = self.predict_hire_probability(candidate, job_requirements)
        
        return sorted(candidates, key=lambda c: c['ml_rank_score'], reverse=True)
    
    def record_hiring_decision(self, candidate: Dict, hired: bool, job_requirements: Dict = None):
        """
        Record a hiring decision for model improvement
        Call this when a candidate is hired or rejected after interview
        """
        features = self.extract_features(candidate, job_requirements)
        
        self.training_history.append({
            'features': features.tolist(),
            'hired': 1 if hired else 0,
            'timestamp': datetime.now().isoformat(),
            'candidate_id': candidate.get('id'),
        })
        
        # Save training history
        history_file = MODEL_PATH / "training_history.json"
        try:
            with open(history_file, 'w') as f:
                json.dump(self.training_history, f)
        except Exception as e:
            logger.error(f"Could not save training history: {e}")
        
        # Retrain if we have enough new data
        if len(self.training_history) >= 50 and len(self.training_history) % 10 == 0:
            self.retrain()
    
    def retrain(self):
        """Retrain model with accumulated hiring decisions"""
        if len(self.training_history) < 20:
            logger.info("Not enough training data yet (need 20+ decisions)")
            return
        
        try:
            from sklearn.ensemble import GradientBoostingClassifier
            
            # Prepare training data
            X = np.vstack([np.array(h['features']) for h in self.training_history])
            y = np.array([h['hired'] for h in self.training_history])
            
            # Check class balance
            if len(np.unique(y)) < 2:
                logger.warning("Need both hired and not-hired examples to train")
                return
            
            # Retrain
            X_scaled = self.scaler.fit_transform(X)
            self.model = GradientBoostingClassifier(
                n_estimators=100,
                learning_rate=0.1,
                max_depth=5,
                random_state=42
            )
            self.model.fit(X_scaled, y)
            
            # Save
            self._save_model()
            
            logger.info(f"✅ Retrained model on {len(self.training_history)} decisions")
            
        except Exception as e:
            logger.error(f"Retraining failed: {e}")
    
    def get_feature_importance(self) -> Dict[str, float]:
        """Get feature importance for explainability"""
        if self.model is None:
            return {}
        
        try:
            importances = self.model.feature_importances_
            return dict(zip(self.feature_names, importances.tolist()))
        except:
            return {}


# Singleton instance
_ranking_model = None

def get_ranking_model() -> ResumeRankingModel:
    """Get singleton ranking model instance"""
    global _ranking_model
    if _ranking_model is None:
        _ranking_model = ResumeRankingModel()
    return _ranking_model
