/**
 * Email Templates Manager
 * Create, edit, and manage email templates for campaigns
 */
import React, { useState, useEffect } from 'react';
import { advancedApi } from '@/services/api';
import {
  Mail,
  Plus,
  Edit2,
  Trash2,
  Eye,
  Save,
  X,
  Tag,
  AlertCircle,
} from 'lucide-react';

interface Template {
  template_id: string;
  name: string;
  subject: string;
  body: string;
  category: string;
  variables: string[];
  is_default: boolean;
  created_at?: string;
}

const TemplatesManager: React.FC = () => {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedTemplate, setSelectedTemplate] = useState<Template | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const [previewContent, setPreviewContent] = useState<{ subject: string; body: string } | null>(null);
  const [_previewVariables, setPreviewVariables] = useState<Record<string, string>>({});

  // Form state
  const [formData, setFormData] = useState({
    template_id: '',
    name: '',
    subject: '',
    body: '',
    category: 'general',
  });

  useEffect(() => {
    fetchTemplates();
  }, []);

  const fetchTemplates = async () => {
    try {
      const response = await advancedApi.templates.list();
      if (response.data) {
        setTemplates((response.data as { templates: Template[] }).templates || []);
      }
    } catch (error) {
      console.error('Failed to fetch templates:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    try {
      const response = await advancedApi.templates.create(formData);
      if (response.data) {
        setTemplates([...templates, response.data as Template]);
        setIsCreating(false);
        resetForm();
      }
    } catch (error) {
      console.error('Failed to create template:', error);
    }
  };

  const handleUpdate = async () => {
    if (!selectedTemplate) return;
    try {
      const response = await advancedApi.templates.update(selectedTemplate.template_id, {
        name: formData.name,
        subject: formData.subject,
        body: formData.body,
        category: formData.category,
      });
      if (response.data) {
        setTemplates(templates.map(t =>
          t.template_id === selectedTemplate.template_id ? { ...t, ...(response.data as Template) } : t
        ));
        setIsEditing(false);
        setSelectedTemplate(null);
        resetForm();
      }
    } catch (error) {
      console.error('Failed to update template:', error);
    }
  };

  const handleDelete = async (templateId: string) => {
    if (!confirm('Are you sure you want to delete this template?')) return;
    try {
      const response = await advancedApi.templates.delete(templateId);
      if (response.data) {
        setTemplates(templates.filter(t => t.template_id !== templateId));
        if (selectedTemplate?.template_id === templateId) {
          setSelectedTemplate(null);
        }
      }
    } catch (error) {
      console.error('Failed to delete template:', error);
    }
  };

  const handlePreview = async (template: Template) => {
    // Set default preview variables
    const defaultVars: Record<string, string> = {
      candidate_name: 'John Doe',
      first_name: 'John',
      job_title: 'Software Engineer',
      company_name: 'Acme Corp',
      interview_date: 'Monday, Jan 15th at 2:00 PM',
      interviewer_name: 'Jane Smith',
    };
    setPreviewVariables(defaultVars);
    setSelectedTemplate(template);
    setShowPreview(true);

    try {
      const response = await advancedApi.templates.render(template.template_id, defaultVars);
      if (response.data) {
        setPreviewContent(response.data as { subject: string; body: string });
      }
    } catch (error) {
      console.error('Failed to render preview:', error);
    }
  };

  const resetForm = () => {
    setFormData({
      template_id: '',
      name: '',
      subject: '',
      body: '',
      category: 'general',
    });
  };

  const startEdit = (template: Template) => {
    setSelectedTemplate(template);
    setFormData({
      template_id: template.template_id,
      name: template.name,
      subject: template.subject,
      body: template.body,
      category: template.category,
    });
    setIsEditing(true);
  };

  const startCreate = () => {
    resetForm();
    setIsCreating(true);
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
            <Mail className="h-5 w-5 text-blue-600" />
            Email Templates
          </h2>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Manage email templates for campaigns and outreach
          </p>
        </div>
        <button
          onClick={startCreate}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
        >
          <Plus className="h-4 w-4" />
          New Template
        </button>
      </div>

      {/* Templates Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {templates.map((template) => (
          <div
            key={template.template_id}
            className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4"
          >
            <div className="flex items-start justify-between mb-3">
              <div>
                <h3 className="font-medium text-gray-900 dark:text-white">{template.name}</h3>
                <span className="inline-flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 mt-1">
                  <Tag className="h-3 w-3" />
                  {template.category}
                </span>
              </div>
              {template.is_default && (
                <span className="px-2 py-0.5 bg-blue-100 text-blue-700 text-xs rounded-full">
                  Default
                </span>
              )}
            </div>

            <p className="text-sm text-gray-600 dark:text-gray-300 mb-3 line-clamp-2">
              <strong>Subject:</strong> {template.subject}
            </p>

            <div className="flex items-center gap-2">
              <button
                onClick={() => handlePreview(template)}
                className="flex-1 flex items-center justify-center gap-1 px-3 py-1.5 text-sm text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600"
              >
                <Eye className="h-3.5 w-3.5" />
                Preview
              </button>
              <button
                onClick={() => startEdit(template)}
                className="flex items-center justify-center gap-1 px-3 py-1.5 text-sm text-blue-700 bg-blue-100 rounded-lg hover:bg-blue-200"
              >
                <Edit2 className="h-3.5 w-3.5" />
              </button>
              {!template.is_default && (
                <button
                  onClick={() => handleDelete(template.template_id)}
                  className="flex items-center justify-center gap-1 px-3 py-1.5 text-sm text-red-700 bg-red-100 rounded-lg hover:bg-red-200"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Create/Edit Modal */}
      {(isCreating || isEditing) && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl max-w-2xl w-full mx-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between p-4 border-b dark:border-gray-700">
              <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
                {isCreating ? 'Create Template' : 'Edit Template'}
              </h3>
              <button
                onClick={() => {
                  setIsCreating(false);
                  setIsEditing(false);
                  resetForm();
                }}
                className="text-gray-500 hover:text-gray-700"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="p-4 space-y-4">
              {isCreating && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Template ID
                  </label>
                  <input
                    type="text"
                    value={formData.template_id}
                    onChange={(e) => setFormData({ ...formData, template_id: e.target.value.toLowerCase().replace(/\s/g, '_') })}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                    placeholder="e.g., follow_up_email"
                  />
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Template Name
                </label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                  placeholder="e.g., Follow Up Email"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Category
                </label>
                <select
                  value={formData.category}
                  onChange={(e) => setFormData({ ...formData, category: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                >
                  <option value="general">General</option>
                  <option value="outreach">Outreach</option>
                  <option value="interview">Interview</option>
                  <option value="offer">Offer</option>
                  <option value="rejection">Rejection</option>
                  <option value="follow_up">Follow Up</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Subject Line
                </label>
                <input
                  type="text"
                  value={formData.subject}
                  onChange={(e) => setFormData({ ...formData, subject: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                  placeholder="e.g., Following Up on Your Application for {{job_title}}"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Email Body
                </label>
                <textarea
                  value={formData.body}
                  onChange={(e) => setFormData({ ...formData, body: e.target.value })}
                  rows={10}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white font-mono text-sm"
                  placeholder="Dear {{candidate_name}},&#10;&#10;..."
                />
              </div>

              <div className="bg-blue-50 dark:bg-blue-900/20 p-3 rounded-lg">
                <div className="flex items-start gap-2">
                  <AlertCircle className="h-4 w-4 text-blue-600 mt-0.5" />
                  <div className="text-sm text-blue-800 dark:text-blue-300">
                    <strong>Variables:</strong> Use {"{{variable_name}}"} for dynamic content.
                    <br />
                    Common: candidate_name, first_name, job_title, company_name, interview_date
                  </div>
                </div>
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 p-4 border-t dark:border-gray-700">
              <button
                onClick={() => {
                  setIsCreating(false);
                  setIsEditing(false);
                  resetForm();
                }}
                className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
              >
                Cancel
              </button>
              <button
                onClick={isCreating ? handleCreate : handleUpdate}
                className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
              >
                <Save className="h-4 w-4" />
                {isCreating ? 'Create' : 'Save Changes'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Preview Modal */}
      {showPreview && previewContent && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl max-w-2xl w-full mx-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between p-4 border-b dark:border-gray-700">
              <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
                Preview: {selectedTemplate?.name}
              </h3>
              <button
                onClick={() => {
                  setShowPreview(false);
                  setPreviewContent(null);
                }}
                className="text-gray-500 hover:text-gray-700"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="p-4 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-500 mb-1">Subject</label>
                <p className="text-gray-900 dark:text-white font-medium">{previewContent.subject}</p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-500 mb-1">Body</label>
                <div className="bg-gray-50 dark:bg-gray-700 p-4 rounded-lg">
                  <pre className="whitespace-pre-wrap text-gray-900 dark:text-white font-sans text-sm">
                    {previewContent.body}
                  </pre>
                </div>
              </div>
            </div>

            <div className="flex items-center justify-end p-4 border-t dark:border-gray-700">
              <button
                onClick={() => {
                  setShowPreview(false);
                  setPreviewContent(null);
                }}
                className="px-4 py-2 bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default TemplatesManager;
