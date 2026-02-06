"""Test the enhanced AI service"""
import asyncio
from services.local_ai_service import get_local_ai_service

def test_ai():
    ai = get_local_ai_service()

    # Test resume text
    resume = '''
    John Smith
    Senior Software Engineer with 8 years of experience

    Contact: john.smith@email.com | +1 (555) 123-4567 | San Francisco, CA
    LinkedIn: linkedin.com/in/johnsmith

    EDUCATION
    Bachelor of Science in Computer Science from Stanford University, 2016

    SKILLS
    Python, Java, JavaScript, React, Node.js, AWS, Docker, Kubernetes, PostgreSQL

    EXPERIENCE
    Senior Software Engineer at Google (2020 - Present)
    - Led team of 5 engineers to build microservices architecture
    - Improved system performance by 40%
    - Architected cloud-native solutions on AWS

    Software Engineer at Microsoft (2016 - 2020)
    - Developed backend services using Python and Java
    - Managed CI/CD pipelines with Jenkins
    '''

    # Run analysis
    result = asyncio.run(ai.analyze_candidate(resume))
    
    print('=' * 60)
    print('ENHANCED AI ANALYSIS RESULT')
    print('=' * 60)
    print(f"Job Category: {result['job_category']}")
    print(f"Quality Score: {result['quality_score']}%")
    print(f"Experience: {result['experience']} years")
    print(f"Skills ({len(result['skills'])}): {result['skills'][:10]}")
    print(f"Education: {result['education']}")
    print(f"Phone: {result['phone']}")
    print(f"Location: {result['location']}")
    print(f"LinkedIn: {result['linkedin']}")
    print(f"Work Indicators: {result['work_indicators']}")
    print(f"Summary: {result['summary']}")
    print('=' * 60)
    
    # Verify nothing is hardcoded
    assert result['quality_score'] > 0, "Score should be computed from content"
    assert result['quality_score'] != 50, "Score should NOT be hardcoded to 50"
    assert len(result['skills']) > 0, "Should extract skills"
    assert result['experience'] == 8, "Should extract 8 years experience"
    assert len(result['education']) > 0, "Should extract education"
    
    print("✅ All assertions passed - AI is extracting REAL values!")

if __name__ == '__main__':
    test_ai()
