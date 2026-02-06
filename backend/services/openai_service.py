"""
OpenAI Service for AI-powered candidate matching and analysis
"""
import os
from typing import Dict, List, Optional
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

class OpenAIService:
    def __init__(self):
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables")
        
        self.client = OpenAI(api_key=api_key)
        self.model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
        self.max_tokens = int(os.getenv('OPENAI_MAX_TOKENS', '1000'))
        self.temperature = float(os.getenv('OPENAI_TEMPERATURE', '0.7'))
    
    def analyze_candidate_match(
        self, 
        candidate_data: Dict, 
        job_description: Dict
    ) -> Dict:
        """
        Use OpenAI to analyze how well a candidate matches a job description
        """
        prompt = f"""Analyze this candidate against the job requirements:

CANDIDATE:
Name: {candidate_data.get('name', 'N/A')}
Experience: {candidate_data.get('experience', 'N/A')} years
Skills: {', '.join(candidate_data.get('skills', []))}
Education: {candidate_data.get('education', 'N/A')}
Summary: {candidate_data.get('summary', 'N/A')}

JOB REQUIREMENTS:
Title: {job_description.get('title', 'N/A')}
Required Skills: {', '.join(job_description.get('required_skills', []))}
Experience: {job_description.get('experience_required', 'N/A')}
Description: {job_description.get('description', 'N/A')}

Provide:
1. Match score (0-100)
2. Top 3 strengths
3. Top 3 gaps
4. Overall recommendation

Format as JSON with keys: score, strengths (array), gaps (array), recommendation (string)"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert HR recruiter analyzing candidate-job fit. Always respond in valid JSON format."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                response_format={"type": "json_object"}
            )
            
            import json
            result = json.loads(response.choices[0].message.content)
            return result
            
        except Exception as e:
            return {
                "error": str(e),
                "score": 0,
                "strengths": [],
                "gaps": [],
                "recommendation": "Error analyzing candidate"
            }
    
    def generate_interview_questions(
        self,
        candidate_data: Dict,
        job_description: Dict,
        num_questions: int = 5
    ) -> List[str]:
        """
        Generate tailored interview questions for a candidate
        """
        prompt = f"""Generate {num_questions} technical interview questions for this candidate based on their background and the job requirements.

CANDIDATE:
Skills: {', '.join(candidate_data.get('skills', []))}
Experience: {candidate_data.get('experience', 'N/A')} years

JOB:
Title: {job_description.get('title', 'N/A')}
Required Skills: {', '.join(job_description.get('required_skills', []))}

Return as JSON array of questions: {{"questions": ["question 1", "question 2", ...]}}"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert technical interviewer. Always respond in valid JSON format."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                response_format={"type": "json_object"}
            )
            
            import json
            result = json.loads(response.choices[0].message.content)
            return result.get('questions', [])
            
        except Exception as e:
            return [f"Error generating questions: {str(e)}"]
    
    def summarize_resume(self, resume_text: str) -> str:
        """
        Generate a concise summary of a candidate's resume
        """
        prompt = f"""Summarize this resume in 2-3 sentences, highlighting key qualifications:

{resume_text[:2000]}  # Limit to first 2000 chars

Provide a professional, concise summary."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert resume analyst."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=200,
                temperature=0.5
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            return f"Error summarizing resume: {str(e)}"
    
    def chat_with_ai(self, message: str, context: Optional[str] = None) -> str:
        """
        General chat functionality for AI assistant
        """
        messages = [
            {"role": "system", "content": "You are a helpful recruitment AI assistant for a UAE-based hiring platform. Help users find candidates, understand data, and make hiring decisions."}
        ]
        
        if context:
            messages.append({"role": "system", "content": f"Context: {context}"})
        
        messages.append({"role": "user", "content": message})
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            return f"I apologize, but I encountered an error: {str(e)}"

# Singleton instance
_openai_service = None

def get_openai_service() -> Optional[OpenAIService]:
    """Get or create OpenAI service instance"""
    global _openai_service
    if _openai_service is None:
        try:
            _openai_service = OpenAIService()
        except ValueError as e:
            print(f"OpenAI service not available: {e}")
            return None
    return _openai_service
