/**
 * Drip Campaign Manager
 * Create and manage automated follow-up campaigns
 */
import React, { useState, useEffect } from 'react';
import { advancedApi } from '@/services/api';
import {
  Mail,
  MessageSquare,
  Plus,
  Trash2,
  Play,
  Users,
  Clock,
  CheckCircle,
  XCircle,
  ChevronDown,
  ChevronUp,
  Zap,
  Settings,
} from 'lucide-react';

interface CampaignStep {
  delay_days: number;
  delay_hours: number;
  type: 'email' | 'sms' | 'task';
  template?: string;
  message?: string;
  subject?: string;
  condition?: string;
}

interface Campaign {
  campaign_id: string;
  name: string;
  description?: string;
  trigger: string;
  steps: CampaignStep[];
  stop_conditions: string[];
  is_custom?: boolean;
  created_at?: string;
}

interface CampaignStats {
  campaign_id: string;
  total_enrolled: number;
  active: number;
  completed: number;
  cancelled: number;
  responded: number;
}

const CampaignManager: React.FC = () => {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedCampaign, setExpandedCampaign] = useState<string | null>(null);
  const [campaignStats, setCampaignStats] = useState<Record<string, CampaignStats>>({});
  const [isCreating, setIsCreating] = useState(false);

  // Form state for creating campaigns
  const [formData, setFormData] = useState<{
    campaign_id: string;
    name: string;
    description: string;
    trigger: string;
    steps: CampaignStep[];
    stop_conditions: string[];
  }>({
    campaign_id: '',
    name: '',
    description: '',
    trigger: 'manual',
    steps: [],
    stop_conditions: ['responded'],
  });

  useEffect(() => {
    fetchCampaigns();
    fetchAllStats();
  }, []);

  const fetchCampaigns = async () => {
    try {
      const response = await advancedApi.campaigns.list();
      if (response.data) {
        setCampaigns((response.data as { campaigns: Campaign[] }).campaigns || []);
      }
    } catch (error) {
      console.error('Failed to fetch campaigns:', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchAllStats = async () => {
    try {
      const response = await advancedApi.campaigns.getAllStats();
      if (response.data) {
        const data = response.data as { campaigns: Record<string, CampaignStats> };
        setCampaignStats(data.campaigns || {});
      }
    } catch (error) {
      console.error('Failed to fetch stats:', error);
    }
  };

  const handleDelete = async (campaignId: string) => {
    if (!confirm('Are you sure you want to delete this campaign?')) return;
    try {
      await advancedApi.campaigns.delete(campaignId);
      setCampaigns(campaigns.filter(c => c.campaign_id !== campaignId));
    } catch (error) {
      console.error('Failed to delete campaign:', error);
    }
  };

  const handleProcessSteps = async () => {
    try {
      const response = await advancedApi.campaigns.processSteps();
      if (response.data) {
        const result = response.data as { processed: number; errors: number };
        alert(`Processed ${result.processed} steps with ${result.errors} errors`);
        fetchAllStats();
      }
    } catch (error) {
      console.error('Failed to process steps:', error);
    }
  };

  const addStep = () => {
    setFormData({
      ...formData,
      steps: [
        ...formData.steps,
        { delay_days: 1, delay_hours: 0, type: 'email', template: '' },
      ],
    });
  };

  const removeStep = (index: number) => {
    setFormData({
      ...formData,
      steps: formData.steps.filter((_, i) => i !== index),
    });
  };

  const updateStep = (index: number, updates: Partial<CampaignStep>) => {
    setFormData({
      ...formData,
      steps: formData.steps.map((step, i) => (i === index ? { ...step, ...updates } : step)),
    });
  };

  const handleCreate = async () => {
    try {
      const response = await advancedApi.campaigns.create(formData);
      if (response.data) {
        setCampaigns([...campaigns, response.data as Campaign]);
        setIsCreating(false);
        setFormData({
          campaign_id: '',
          name: '',
          description: '',
          trigger: 'manual',
          steps: [],
          stop_conditions: ['responded'],
        });
      }
    } catch (error) {
      console.error('Failed to create campaign:', error);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-gray-900 dark:text-white flex items-center gap-2">
            <Zap className="h-5 w-5 text-yellow-500" />
            Drip Campaigns
          </h2>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Automated follow-up sequences for candidates
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleProcessSteps}
            className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700"
          >
            <Play className="h-4 w-4" />
            Process Due Steps
          </button>
          <button
            onClick={() => setIsCreating(true)}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            New Campaign
          </button>
        </div>
      </div>

      {/* Campaign List */}
      <div className="space-y-4">
        {campaigns.map((campaign) => {
          const stats = campaignStats[campaign.campaign_id];
          const isExpanded = expandedCampaign === campaign.campaign_id;

          return (
            <div
              key={campaign.campaign_id}
              className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden"
            >
              {/* Campaign Header */}
              <div
                className="flex items-center justify-between p-4 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50"
                onClick={() => setExpandedCampaign(isExpanded ? null : campaign.campaign_id)}
              >
                <div className="flex items-center gap-4">
                  <div className="p-2 bg-blue-100 dark:bg-blue-900/30 rounded-lg">
                    <Mail className="h-5 w-5 text-blue-600" />
                  </div>
                  <div>
                    <h3 className="font-medium text-gray-900 dark:text-white">{campaign.name}</h3>
                    <p className="text-sm text-gray-500 dark:text-gray-400">
                      {campaign.steps.length} steps • Trigger: {campaign.trigger}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-4">
                  {stats && (
                    <div className="flex items-center gap-3 text-sm">
                      <span className="flex items-center gap-1 text-blue-600">
                        <Users className="h-4 w-4" />
                        {stats.total_enrolled}
                      </span>
                      <span className="flex items-center gap-1 text-green-600">
                        <CheckCircle className="h-4 w-4" />
                        {stats.completed}
                      </span>
                      <span className="flex items-center gap-1 text-yellow-600">
                        <Clock className="h-4 w-4" />
                        {stats.active}
                      </span>
                    </div>
                  )}
                  {isExpanded ? (
                    <ChevronUp className="h-5 w-5 text-gray-400" />
                  ) : (
                    <ChevronDown className="h-5 w-5 text-gray-400" />
                  )}
                </div>
              </div>

              {/* Expanded Content */}
              {isExpanded && (
                <div className="border-t dark:border-gray-700 p-4 bg-gray-50 dark:bg-gray-900/50">
                  {campaign.description && (
                    <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
                      {campaign.description}
                    </p>
                  )}

                  {/* Steps */}
                  <div className="space-y-3 mb-4">
                    <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300">Steps:</h4>
                    {campaign.steps.map((step, index) => (
                      <div
                        key={index}
                        className="flex items-center gap-3 p-3 bg-white dark:bg-gray-800 rounded-lg"
                      >
                        <div className="flex items-center justify-center w-8 h-8 bg-blue-100 dark:bg-blue-900/30 rounded-full text-sm font-medium text-blue-600">
                          {index + 1}
                        </div>
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
                            {step.type === 'email' ? (
                              <Mail className="h-4 w-4 text-blue-500" />
                            ) : step.type === 'sms' ? (
                              <MessageSquare className="h-4 w-4 text-green-500" />
                            ) : (
                              <Settings className="h-4 w-4 text-gray-500" />
                            )}
                            <span className="text-sm font-medium text-gray-900 dark:text-white capitalize">
                              {step.type}
                            </span>
                            {step.template && (
                              <span className="text-xs text-gray-500">
                                Template: {step.template}
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-gray-500 mt-1">
                            Wait {step.delay_days > 0 ? `${step.delay_days} days` : ''}{' '}
                            {step.delay_hours > 0 ? `${step.delay_hours} hours` : ''}
                            {step.condition && ` • Condition: ${step.condition}`}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Stop Conditions */}
                  {campaign.stop_conditions.length > 0 && (
                    <div className="mb-4">
                      <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                        Stop Conditions:
                      </h4>
                      <div className="flex flex-wrap gap-2">
                        {campaign.stop_conditions.map((condition, index) => (
                          <span
                            key={index}
                            className="px-2 py-1 bg-red-100 text-red-700 text-xs rounded-full"
                          >
                            {condition}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Actions */}
                  <div className="flex items-center gap-2 pt-3 border-t dark:border-gray-700">
                    {campaign.is_custom && (
                      <button
                        onClick={() => handleDelete(campaign.campaign_id)}
                        className="flex items-center gap-1 px-3 py-1.5 text-sm text-red-700 bg-red-100 rounded-lg hover:bg-red-200"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        Delete
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Create Campaign Modal */}
      {isCreating && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between p-4 border-b dark:border-gray-700">
              <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
                Create New Campaign
              </h3>
              <button
                onClick={() => setIsCreating(false)}
                className="text-gray-500 hover:text-gray-700"
              >
                <XCircle className="h-5 w-5" />
              </button>
            </div>

            <div className="p-4 space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Campaign ID
                  </label>
                  <input
                    type="text"
                    value={formData.campaign_id}
                    onChange={(e) =>
                      setFormData({
                        ...formData,
                        campaign_id: e.target.value.toLowerCase().replace(/\s/g, '_'),
                      })
                    }
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                    placeholder="e.g., new_applicant_follow_up"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Campaign Name
                  </label>
                  <input
                    type="text"
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                    placeholder="e.g., New Applicant Follow Up"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Description
                </label>
                <textarea
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                  rows={2}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                  placeholder="What does this campaign do?"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Trigger
                </label>
                <select
                  value={formData.trigger}
                  onChange={(e) => setFormData({ ...formData, trigger: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                >
                  <option value="manual">Manual Enrollment</option>
                  <option value="new_application">New Application</option>
                  <option value="interview_no_show">Interview No-Show</option>
                  <option value="offer_sent">Offer Sent</option>
                  <option value="rejection_sent">Rejection Sent</option>
                </select>
              </div>

              {/* Steps */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                    Campaign Steps
                  </label>
                  <button
                    onClick={addStep}
                    className="flex items-center gap-1 px-2 py-1 text-sm text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded"
                  >
                    <Plus className="h-4 w-4" />
                    Add Step
                  </button>
                </div>

                <div className="space-y-3">
                  {formData.steps.map((step, index) => (
                    <div
                      key={index}
                      className="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg space-y-3"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
                          Step {index + 1}
                        </span>
                        <button
                          onClick={() => removeStep(index)}
                          className="text-red-500 hover:text-red-700"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>

                      <div className="grid grid-cols-3 gap-3">
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Type</label>
                          <select
                            value={step.type}
                            onChange={(e) =>
                              updateStep(index, { type: e.target.value as 'email' | 'sms' | 'task' })
                            }
                            className="w-full px-2 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700"
                          >
                            <option value="email">Email</option>
                            <option value="sms">SMS</option>
                            <option value="task">Task</option>
                          </select>
                        </div>
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Delay (Days)</label>
                          <input
                            type="number"
                            min="0"
                            value={step.delay_days}
                            onChange={(e) =>
                              updateStep(index, { delay_days: parseInt(e.target.value) || 0 })
                            }
                            className="w-full px-2 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700"
                          />
                        </div>
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Delay (Hours)</label>
                          <input
                            type="number"
                            min="0"
                            value={step.delay_hours}
                            onChange={(e) =>
                              updateStep(index, { delay_hours: parseInt(e.target.value) || 0 })
                            }
                            className="w-full px-2 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700"
                          />
                        </div>
                      </div>

                      <div>
                        <label className="block text-xs text-gray-500 mb-1">
                          {step.type === 'email' ? 'Template ID' : step.type === 'sms' ? 'Message' : 'Task Description'}
                        </label>
                        <input
                          type="text"
                          value={step.template || step.message || ''}
                          onChange={(e) =>
                            updateStep(index, step.type === 'email' ? { template: e.target.value } : { message: e.target.value })
                          }
                          className="w-full px-2 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700"
                          placeholder={step.type === 'email' ? 'e.g., follow_up_email' : 'Enter message or task'}
                        />
                      </div>

                      <div>
                        <label className="block text-xs text-gray-500 mb-1">
                          Condition (optional)
                        </label>
                        <select
                          value={step.condition || ''}
                          onChange={(e) => updateStep(index, { condition: e.target.value || undefined })}
                          className="w-full px-2 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700"
                        >
                          <option value="">Always execute</option>
                          <option value="no_response">Only if no response</option>
                          <option value="has_phone">Only if has phone</option>
                          <option value="has_email">Only if has email</option>
                        </select>
                      </div>
                    </div>
                  ))}

                  {formData.steps.length === 0 && (
                    <p className="text-sm text-gray-500 text-center py-4">
                      No steps added. Click "Add Step" to begin.
                    </p>
                  )}
                </div>
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 p-4 border-t dark:border-gray-700">
              <button
                onClick={() => setIsCreating(false)}
                className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={!formData.campaign_id || !formData.name || formData.steps.length === 0}
                className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                <Plus className="h-4 w-4" />
                Create Campaign
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default CampaignManager;
