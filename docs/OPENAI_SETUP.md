# OpenAI Setup Instructions

## 1. Get Your OpenAI API Key

1. Go to [OpenAI Platform](https://platform.openai.com/)
2. Sign in or create an account
3. Navigate to **API Keys** section
4. Click **Create new secret key**
5. Copy the API key (starts with `sk-...`)

## 2. Configure in .env File

Open `backend/.env` and add your API key:

```env
# OpenAI API Configuration
OPENAI_API_KEY=sk-your-actual-api-key-here
OPENAI_MODEL=gpt-4o-mini
OPENAI_MAX_TOKENS=1000
OPENAI_TEMPERATURE=0.7
```

### Model Options:
- **gpt-4o-mini** - Fast, cost-effective (recommended)
- **gpt-4o** - Most capable
- **gpt-3.5-turbo** - Faster, cheaper

## 3. Install Required Package

```bash
cd backend
pip install openai
```

## 4. Available AI Features

Once configured, you'll have access to:

### 1. AI Chat Assistant (`/api/ai/chat`)
- Natural language interaction
- Context-aware responses
- UAE recruitment focused

### 2. Candidate Match Analysis (`/api/ai/analyze-match`)
- AI-powered match scoring (0-100)
- Identify candidate strengths
- Identify skill gaps
- Get hiring recommendations

### 3. Interview Questions (`/api/ai/interview-questions`)
- Generate tailored interview questions
- Based on candidate skills & job requirements
- Technical and behavioral questions

### 4. Resume Summarization (`/api/ai/summarize-resume`)
- Automatic resume summaries
- Highlight key qualifications
- Save time screening candidates

### 5. Status Check (`/api/ai/status`)
- Verify OpenAI integration
- Check configuration

## 5. Frontend Integration

The AI Assistant page (`/ai-assistant`) can be enhanced to use real OpenAI responses:

```typescript
// Example: Call AI chat endpoint
const response = await fetch('http://localhost:8000/api/ai/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    message: userMessage,
    context: JSON.stringify({ candidates, filters })
  })
})
const data = await response.json()
console.log(data.response) // AI response
```

## 6. Cost Estimation

**gpt-4o-mini** pricing:
- Input: $0.150 / 1M tokens
- Output: $0.600 / 1M tokens

Average costs per request:
- Chat: ~$0.001 per message
- Match analysis: ~$0.002 per analysis
- Interview questions: ~$0.003 per set

## 7. Usage Limits

Set limits in OpenAI dashboard:
- Monthly budget caps
- Rate limiting
- Usage alerts

## 8. Verify Setup

```bash
# Start backend
cd backend
python main.py

# Test AI endpoint
curl http://localhost:8000/api/ai/status
```

Should return:
```json
{
  "available": true,
  "model": "gpt-4o-mini",
  "message": "OpenAI service active"
}
```

## Troubleshooting

**Error: "OPENAI_API_KEY not found"**
- Ensure `.env` file has `OPENAI_API_KEY=sk-...`
- Restart backend server

**Error: "Rate limit exceeded"**
- Check OpenAI dashboard for usage limits
- Upgrade plan or wait for reset

**Error: "Invalid API key"**
- Verify key is correct (starts with `sk-`)
- Generate new key in OpenAI dashboard
