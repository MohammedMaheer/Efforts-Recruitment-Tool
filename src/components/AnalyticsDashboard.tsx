/**
 * Advanced Analytics Dashboard
 * Displays predictive analytics, campaign stats, and ML insights
 */
import React, { useState, useEffect } from 'react';
import { advancedApi } from '@/services/api';
import {
  BarChart3,
  TrendingUp,
  Users,
  Mail,
  MessageSquare,
  Calendar,
  Target,
  AlertTriangle,
  CheckCircle,
  Zap,
  Brain,
  RefreshCw,
} from 'lucide-react';

interface CampaignStats {
  campaign_id: string;
  total_enrolled: number;
  active: number;
  completed: number;
  cancelled: number;
  responded: number;
}

interface PipelineAnalytics {
  total_candidates: number;
  avg_response_rate: number;
  avg_interview_success: number;
  bottlenecks: string[];
  recommendations: string[];
}

interface AllCampaignStats {
  total_campaigns: number;
  total_enrollments: number;
  active_enrollments: number;
  campaigns: Record<string, CampaignStats>;
}

const AnalyticsDashboard: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [campaignStats, setCampaignStats] = useState<AllCampaignStats | null>(null);
  const [pipelineAnalytics, setPipelineAnalytics] = useState<PipelineAnalytics | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const fetchData = async () => {
    try {
      const [campaignsRes, pipelineRes] = await Promise.all([
        advancedApi.campaigns.getAllStats(),
        advancedApi.analytics.getPipelineAnalytics(),
      ]);

      if (campaignsRes.data) {
        setCampaignStats(campaignsRes.data as AllCampaignStats);
      }
      if (pipelineRes.data) {
        setPipelineAnalytics(pipelineRes.data as PipelineAnalytics);
      }
    } catch (error) {
      console.error('Failed to fetch analytics:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleRefresh = async () => {
    setRefreshing(true);
    await fetchData();
    setRefreshing(false);
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
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <Brain className="h-7 w-7 text-purple-600" />
            AI Analytics Dashboard
          </h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">
            Predictive insights and campaign performance
          </p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Top Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Active Campaigns"
          value={campaignStats?.total_campaigns || 0}
          icon={<Mail className="h-5 w-5" />}
          color="blue"
        />
        <StatCard
          title="Total Enrollments"
          value={campaignStats?.total_enrollments || 0}
          icon={<Users className="h-5 w-5" />}
          color="green"
        />
        <StatCard
          title="Active Enrollments"
          value={campaignStats?.active_enrollments || 0}
          icon={<Zap className="h-5 w-5" />}
          color="yellow"
        />
        <StatCard
          title="Avg Response Rate"
          value={`${((pipelineAnalytics?.avg_response_rate || 0) * 100).toFixed(0)}%`}
          icon={<TrendingUp className="h-5 w-5" />}
          color="purple"
        />
      </div>

      {/* Campaign Performance */}
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4 flex items-center gap-2">
          <BarChart3 className="h-5 w-5 text-blue-600" />
          Campaign Performance
        </h2>
        
        {campaignStats?.campaigns && Object.keys(campaignStats.campaigns).length > 0 ? (
          <div className="space-y-4">
            {Object.entries(campaignStats.campaigns).map(([campaignId, stats]) => (
              <CampaignRow key={campaignId} campaignId={campaignId} stats={stats} />
            ))}
          </div>
        ) : (
          <div className="text-center py-8 text-gray-500">
            <Mail className="h-12 w-12 mx-auto mb-2 opacity-50" />
            <p>No campaign data yet. Start enrolling candidates in campaigns!</p>
          </div>
        )}
      </div>

      {/* Pipeline Insights */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Bottlenecks */}
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4 flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-orange-500" />
            Pipeline Bottlenecks
          </h2>
          
          {pipelineAnalytics?.bottlenecks && pipelineAnalytics.bottlenecks.length > 0 ? (
            <ul className="space-y-2">
              {pipelineAnalytics.bottlenecks.map((bottleneck, index) => (
                <li key={index} className="flex items-start gap-2 text-gray-700 dark:text-gray-300">
                  <AlertTriangle className="h-4 w-4 text-orange-500 mt-0.5 flex-shrink-0" />
                  <span>{bottleneck}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-gray-500 text-center py-4">
              <CheckCircle className="h-8 w-8 mx-auto mb-2 text-green-500" />
              No bottlenecks detected!
            </p>
          )}
        </div>

        {/* Recommendations */}
        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4 flex items-center gap-2">
            <Target className="h-5 w-5 text-green-500" />
            AI Recommendations
          </h2>
          
          {pipelineAnalytics?.recommendations && pipelineAnalytics.recommendations.length > 0 ? (
            <ul className="space-y-2">
              {pipelineAnalytics.recommendations.map((rec, index) => (
                <li key={index} className="flex items-start gap-2 text-gray-700 dark:text-gray-300">
                  <CheckCircle className="h-4 w-4 text-green-500 mt-0.5 flex-shrink-0" />
                  <span>{rec}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-gray-500 text-center py-4">
              Recommendations will appear as you process more candidates.
            </p>
          )}
        </div>
      </div>

      {/* Quick Actions */}
      <div className="bg-gradient-to-r from-blue-600 to-purple-600 rounded-xl p-6 text-white">
        <h2 className="text-lg font-semibold mb-4">Quick Actions</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <QuickActionButton
            icon={<Brain className="h-5 w-5" />}
            label="Retrain ML Model"
            onClick={() => advancedApi.ml.retrain()}
          />
          <QuickActionButton
            icon={<Mail className="h-5 w-5" />}
            label="Process Campaigns"
            onClick={() => advancedApi.campaigns.processSteps()}
          />
          <QuickActionButton
            icon={<Calendar className="h-5 w-5" />}
            label="View Templates"
            onClick={() => window.location.href = '/settings?tab=templates'}
          />
          <QuickActionButton
            icon={<MessageSquare className="h-5 w-5" />}
            label="SMS Templates"
            onClick={() => window.location.href = '/settings?tab=sms'}
          />
        </div>
      </div>
    </div>
  );
};

// Sub-components
interface StatCardProps {
  title: string;
  value: string | number;
  icon: React.ReactNode;
  color: 'blue' | 'green' | 'yellow' | 'purple' | 'red';
}

const StatCard: React.FC<StatCardProps> = ({ title, value, icon, color }) => {
  const colors = {
    blue: 'bg-blue-100 text-blue-600 dark:bg-blue-900/30',
    green: 'bg-green-100 text-green-600 dark:bg-green-900/30',
    yellow: 'bg-yellow-100 text-yellow-600 dark:bg-yellow-900/30',
    purple: 'bg-purple-100 text-purple-600 dark:bg-purple-900/30',
    red: 'bg-red-100 text-red-600 dark:bg-red-900/30',
  };

  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-gray-500 dark:text-gray-400">{title}</p>
          <p className="text-2xl font-bold text-gray-900 dark:text-white mt-1">{value}</p>
        </div>
        <div className={`p-3 rounded-lg ${colors[color]}`}>
          {icon}
        </div>
      </div>
    </div>
  );
};

interface CampaignRowProps {
  campaignId: string;
  stats: CampaignStats;
}

const CampaignRow: React.FC<CampaignRowProps> = ({ campaignId, stats }) => {
  const responseRate = stats.total_enrolled > 0
    ? ((stats.responded / stats.total_enrolled) * 100).toFixed(1)
    : '0';

  return (
    <div className="flex items-center justify-between p-4 bg-gray-50 dark:bg-gray-700/50 rounded-lg">
      <div>
        <h3 className="font-medium text-gray-900 dark:text-white">
          {campaignId.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
        </h3>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          {stats.total_enrolled} enrolled • {stats.active} active • {responseRate}% response rate
        </p>
      </div>
      <div className="flex items-center gap-4">
        <div className="text-center">
          <p className="text-lg font-semibold text-green-600">{stats.completed}</p>
          <p className="text-xs text-gray-500">Completed</p>
        </div>
        <div className="text-center">
          <p className="text-lg font-semibold text-blue-600">{stats.responded}</p>
          <p className="text-xs text-gray-500">Responded</p>
        </div>
      </div>
    </div>
  );
};

interface QuickActionButtonProps {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}

const QuickActionButton: React.FC<QuickActionButtonProps> = ({ icon, label, onClick }) => (
  <button
    onClick={onClick}
    className="flex flex-col items-center gap-2 p-4 bg-white/10 hover:bg-white/20 rounded-lg transition-colors"
  >
    {icon}
    <span className="text-sm font-medium">{label}</span>
  </button>
);

export default AnalyticsDashboard;
