/**
 * Candidate AI Insights Panel
 * Displays ML ranking, quality analysis, predictions, and skill gaps
 */
import React, { useState, useEffect } from 'react';
import { advancedApi } from '@/services/api';
import {
  Brain,
  Target,
  AlertTriangle,
  CheckCircle,
  TrendingUp,
  Clock,
  Sparkles,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  Mail,
  MessageSquare,
  Calendar,
} from 'lucide-react';

interface CandidateInsightsProps {
  candidateId: string;
  candidateName: string;
  candidateEmail: string;
  candidatePhone?: string;
  jobId?: string;
}

interface Prediction {
  response_rate: number;
  interview_success: number;
  offer_acceptance: number;
  retention_risk: string;
  time_to_hire_days: number;
}

interface QualityAnalysis {
  overall_score: number;
  red_flags: Array<{ type: string; severity: string; description: string }>;
  strengths: string[];
  ats_score: number;
  interview_questions: string[];
}

interface MLRanking {
  hire_probability: number;
  rank: number;
  factors: Record<string, number>;
}

const CandidateAIInsights: React.FC<CandidateInsightsProps> = ({
  candidateId,
  candidateName,
  candidateEmail,
  candidatePhone,
  jobId,
}) => {
  const [loading, setLoading] = useState(true);
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [quality, setQuality] = useState<QualityAnalysis | null>(null);
  const [mlRanking, setMlRanking] = useState<MLRanking | null>(null);
  const [expandedSection, setExpandedSection] = useState<string | null>('predictions');
  const [enrolling, setEnrolling] = useState(false);

  useEffect(() => {
    fetchInsights();
  }, [candidateId, jobId]);

  const fetchInsights = async () => {
    setLoading(true);
    try {
      const [predRes, qualityRes, mlRes] = await Promise.all([
        advancedApi.analytics.predict(candidateId, jobId),
        advancedApi.quality.analyze({ candidateId }),
        advancedApi.ml.rankCandidates([candidateId], jobId, 1),
      ]);

      if (predRes.data) {
        setPrediction(predRes.data as Prediction);
      }
      if (qualityRes.data) {
        setQuality(qualityRes.data as QualityAnalysis);
      }
      if (mlRes.data) {
        const data = mlRes.data as { rankings: MLRanking[] };
        if (data.rankings?.[0]) {
          setMlRanking(data.rankings[0]);
        }
      }
    } catch (error) {
      console.error('Failed to fetch insights:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleEnrollCampaign = async (campaignId: string) => {
    setEnrolling(true);
    try {
      await advancedApi.campaigns.enroll({
        candidateId,
        candidateEmail,
        candidateName,
        candidatePhone,
        campaignId,
      });
      alert(`${candidateName} enrolled in campaign!`);
    } catch (error) {
      console.error('Failed to enroll:', error);
      alert('Failed to enroll in campaign');
    } finally {
      setEnrolling(false);
    }
  };

  const handleSendSMS = async () => {
    if (!candidatePhone) {
      alert('No phone number available');
      return;
    }
    try {
      await advancedApi.sms.send({
        toPhone: candidatePhone,
        templateId: 'application_received',
        variables: { name: candidateName.split(' ')[0] },
        candidateId,
      });
      alert('SMS sent!');
    } catch (error) {
      console.error('Failed to send SMS:', error);
      alert('Failed to send SMS');
    }
  };

  const toggleSection = (section: string) => {
    setExpandedSection(expandedSection === section ? null : section);
  };

  if (loading) {
    return (
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6">
        <div className="flex items-center justify-center h-32">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b dark:border-gray-700 bg-gradient-to-r from-purple-600 to-blue-600">
        <div className="flex items-center gap-2 text-white">
          <Brain className="h-5 w-5" />
          <h3 className="font-semibold">AI Insights</h3>
        </div>
        <button
          onClick={fetchInsights}
          className="p-1.5 text-white/80 hover:text-white hover:bg-white/10 rounded"
        >
          <RefreshCw className="h-4 w-4" />
        </button>
      </div>

      {/* ML Score */}
      {mlRanking && (
        <div className="p-4 border-b dark:border-gray-700">
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-600 dark:text-gray-400">Hire Probability</span>
            <span className="text-2xl font-bold text-purple-600">
              {(mlRanking.hire_probability * 100).toFixed(0)}%
            </span>
          </div>
          <div className="mt-2 h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-purple-500 to-blue-500 rounded-full transition-all"
              style={{ width: `${mlRanking.hire_probability * 100}%` }}
            />
          </div>
        </div>
      )}

      {/* Predictions Section */}
      {prediction && (
        <div className="border-b dark:border-gray-700">
          <button
            onClick={() => toggleSection('predictions')}
            className="w-full flex items-center justify-between p-4 hover:bg-gray-50 dark:hover:bg-gray-700/50"
          >
            <div className="flex items-center gap-2">
              <TrendingUp className="h-4 w-4 text-blue-500" />
              <span className="font-medium text-gray-900 dark:text-white">Predictions</span>
            </div>
            {expandedSection === 'predictions' ? (
              <ChevronUp className="h-4 w-4 text-gray-400" />
            ) : (
              <ChevronDown className="h-4 w-4 text-gray-400" />
            )}
          </button>

          {expandedSection === 'predictions' && (
            <div className="px-4 pb-4 space-y-3">
              <PredictionRow
                label="Response Rate"
                value={prediction.response_rate}
                color="blue"
              />
              <PredictionRow
                label="Interview Success"
                value={prediction.interview_success}
                color="green"
              />
              <PredictionRow
                label="Offer Acceptance"
                value={prediction.offer_acceptance}
                color="purple"
              />
              <div className="flex items-center justify-between">
                <span className="text-sm text-gray-600 dark:text-gray-400">Retention Risk</span>
                <span
                  className={`px-2 py-0.5 text-xs rounded-full ${
                    prediction.retention_risk === 'low'
                      ? 'bg-green-100 text-green-700'
                      : prediction.retention_risk === 'medium'
                      ? 'bg-yellow-100 text-yellow-700'
                      : 'bg-red-100 text-red-700'
                  }`}
                >
                  {prediction.retention_risk}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-gray-600 dark:text-gray-400">Est. Time to Hire</span>
                <span className="text-sm font-medium text-gray-900 dark:text-white flex items-center gap-1">
                  <Clock className="h-3.5 w-3.5" />
                  {prediction.time_to_hire_days} days
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Quality Analysis Section */}
      {quality && (
        <div className="border-b dark:border-gray-700">
          <button
            onClick={() => toggleSection('quality')}
            className="w-full flex items-center justify-between p-4 hover:bg-gray-50 dark:hover:bg-gray-700/50"
          >
            <div className="flex items-center gap-2">
              <Target className="h-4 w-4 text-green-500" />
              <span className="font-medium text-gray-900 dark:text-white">Quality Analysis</span>
              <span
                className={`px-2 py-0.5 text-xs rounded-full ${
                  quality.overall_score >= 70
                    ? 'bg-green-100 text-green-700'
                    : quality.overall_score >= 50
                    ? 'bg-yellow-100 text-yellow-700'
                    : 'bg-red-100 text-red-700'
                }`}
              >
                {quality.overall_score}%
              </span>
            </div>
            {expandedSection === 'quality' ? (
              <ChevronUp className="h-4 w-4 text-gray-400" />
            ) : (
              <ChevronDown className="h-4 w-4 text-gray-400" />
            )}
          </button>

          {expandedSection === 'quality' && (
            <div className="px-4 pb-4 space-y-4">
              {/* ATS Score */}
              <div>
                <div className="flex items-center justify-between text-sm mb-1">
                  <span className="text-gray-600 dark:text-gray-400">ATS Compatibility</span>
                  <span className="font-medium">{quality.ats_score}%</span>
                </div>
                <div className="h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 rounded-full"
                    style={{ width: `${quality.ats_score}%` }}
                  />
                </div>
              </div>

              {/* Red Flags */}
              {quality.red_flags.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 flex items-center gap-1">
                    <AlertTriangle className="h-3.5 w-3.5 text-orange-500" />
                    Red Flags ({quality.red_flags.length})
                  </h4>
                  <div className="space-y-1">
                    {quality.red_flags.slice(0, 3).map((flag, index) => (
                      <div
                        key={index}
                        className="text-xs p-2 bg-red-50 dark:bg-red-900/20 rounded text-red-700 dark:text-red-300"
                      >
                        <span className="font-medium">{flag.type}:</span> {flag.description}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Strengths */}
              {quality.strengths.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 flex items-center gap-1">
                    <CheckCircle className="h-3.5 w-3.5 text-green-500" />
                    Strengths
                  </h4>
                  <div className="flex flex-wrap gap-1">
                    {quality.strengths.slice(0, 5).map((strength, index) => (
                      <span
                        key={index}
                        className="px-2 py-0.5 bg-green-100 dark:bg-green-900/20 text-green-700 dark:text-green-300 text-xs rounded-full"
                      >
                        {strength}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Interview Questions */}
              {quality.interview_questions.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 flex items-center gap-1">
                    <Sparkles className="h-3.5 w-3.5 text-purple-500" />
                    Suggested Questions
                  </h4>
                  <ul className="space-y-1 text-xs text-gray-600 dark:text-gray-400">
                    {quality.interview_questions.slice(0, 3).map((q, index) => (
                      <li key={index} className="flex items-start gap-1">
                        <span className="text-purple-500">•</span>
                        {q}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Quick Actions */}
      <div className="p-4 bg-gray-50 dark:bg-gray-900/50">
        <h4 className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-3">
          Quick Actions
        </h4>
        <div className="grid grid-cols-3 gap-2">
          <button
            onClick={() => handleEnrollCampaign('new_applicant_nurture')}
            disabled={enrolling}
            className="flex flex-col items-center gap-1 p-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg hover:border-blue-500 text-xs"
          >
            <Mail className="h-4 w-4 text-blue-500" />
            <span>Nurture</span>
          </button>
          <button
            onClick={handleSendSMS}
            disabled={!candidatePhone}
            className="flex flex-col items-center gap-1 p-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg hover:border-green-500 text-xs disabled:opacity-50"
          >
            <MessageSquare className="h-4 w-4 text-green-500" />
            <span>SMS</span>
          </button>
          <button
            onClick={() => window.location.href = `/schedule?candidate=${candidateId}`}
            className="flex flex-col items-center gap-1 p-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg hover:border-purple-500 text-xs"
          >
            <Calendar className="h-4 w-4 text-purple-500" />
            <span>Schedule</span>
          </button>
        </div>
      </div>
    </div>
  );
};

// Helper Components
interface PredictionRowProps {
  label: string;
  value: number;
  color: 'blue' | 'green' | 'purple' | 'yellow' | 'red';
}

const PredictionRow: React.FC<PredictionRowProps> = ({ label, value, color }) => {
  const colors = {
    blue: 'bg-blue-500',
    green: 'bg-green-500',
    purple: 'bg-purple-500',
    yellow: 'bg-yellow-500',
    red: 'bg-red-500',
  };

  return (
    <div>
      <div className="flex items-center justify-between text-sm mb-1">
        <span className="text-gray-600 dark:text-gray-400">{label}</span>
        <span className="font-medium">{(value * 100).toFixed(0)}%</span>
      </div>
      <div className="h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div
          className={`h-full ${colors[color]} rounded-full transition-all`}
          style={{ width: `${value * 100}%` }}
        />
      </div>
    </div>
  );
};

export default CandidateAIInsights;
