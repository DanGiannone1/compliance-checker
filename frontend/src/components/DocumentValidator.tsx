"use client";

import React, { useState, useEffect } from 'react';
import { Upload, File, Play, CheckCircle, AlertCircle, Loader, AlertTriangle, Info, Shield, ChevronDown, ChevronUp, Filter } from 'lucide-react';
import ReactMarkdown from 'react-markdown';

// Enhanced type definitions
interface Finding {
  content: string;
  severity: 'critical' | 'warning' | 'info';
  category: string;
}

interface ValidationResult {
  findings: Finding[];
  success: boolean;
  message: string;
}

interface UploadResponse {
  success: boolean;
  message: string;
  file_id: string;
  file_path: string;
  file_size: number;
  original_filename: string;
}

interface FileInfo {
  file_path: string;
  original_filename: string;
  file_size: number;
  upload_date: string;
}

interface FileWithStatus {
  file?: File; // Optional for existing files loaded from server
  status: 'uploading' | 'completed' | 'error' | 'removing';
  id: string;
  file_path?: string; // ADLS file path returned from upload
  file_id?: string;   // Unique file ID from backend
  error_message?: string;
  original_filename: string;
  file_size: number;
  upload_date?: string;
  is_existing?: boolean; // Flag for files loaded from server
}

const API_BASE_URL = 'http://localhost:8000';

const DocumentValidator: React.FC = () => {
  const [inputFile, setInputFile] = useState<FileWithStatus | null>(null);
  const [referenceFiles, setReferenceFiles] = useState<FileWithStatus[]>([]);
  const [instructions, setInstructions] = useState<string>('');
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [results, setResults] = useState<ValidationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedSeverity, setSelectedSeverity] = useState<string>('all');
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());

  // Improved severity configuration
  const severityConfig = {
    critical: {
      icon: AlertCircle,
      color: 'text-red-500',
      bgColor: 'bg-red-50',
      borderColor: 'border-red-200',
      badgeColor: 'bg-red-100 text-red-800',
      label: 'Critical',
      description: 'Immediate attention required'
    },
    warning: {
      icon: AlertTriangle,
      color: 'text-amber-500',
      bgColor: 'bg-amber-50',
      borderColor: 'border-amber-200',
      badgeColor: 'bg-amber-100 text-amber-800',
      label: 'Warning',
      description: 'Should be addressed'
    },
    info: {
      icon: CheckCircle,
      color: 'text-emerald-500',
      bgColor: 'bg-emerald-50',
      borderColor: 'border-emerald-200',
      badgeColor: 'bg-emerald-100 text-emerald-800',
      label: 'Info',
      description: 'For your information'
    }
  };

  const generateFileId = () => Math.random().toString(36).substr(2, 9);

  // Load existing files on component mount
  useEffect(() => {
    loadExistingFiles();
  }, []);

  const loadExistingFiles = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/files/all`);
      if (response.ok) {
        const data = await response.json();
        
        // Convert server files to FileWithStatus format
        const convertToFileWithStatus = (fileInfo: FileInfo, type: string): FileWithStatus => ({
          status: 'completed' as const,
          id: generateFileId(),
          file_path: fileInfo.file_path,
          original_filename: fileInfo.original_filename,
          file_size: fileInfo.file_size,
          upload_date: fileInfo.upload_date,
          is_existing: true
        });

        // Load input files
        if (data.input_files && data.input_files.length > 0) {
          // For input files, only take the first one (since we only support one input file)
          const inputFile = convertToFileWithStatus(data.input_files[0], 'input');
          setInputFile(inputFile);
        }

        // Load reference files
        if (data.reference_files && data.reference_files.length > 0) {
          const refFiles = data.reference_files.map((fileInfo: FileInfo) => 
            convertToFileWithStatus(fileInfo, 'reference')
          );
          setReferenceFiles(refFiles);
        }
      }
    } catch (error) {
      console.error('Error loading existing files:', error);
    }
  };

  const handleFileUpload = (file: File, type: string): void => {
    const fileId = generateFileId();
    const fileWithStatus: FileWithStatus = {
      file,
      status: 'uploading',
      id: fileId,
      original_filename: file.name,
      file_size: file.size
    };

    if (type === 'input') {
      setInputFile(fileWithStatus);
    } else if (type === 'reference') {
      setReferenceFiles(prev => [...prev, fileWithStatus]);
    }

    // Start the actual upload
    uploadFileToBackend(file, type, fileId);
  };

  const uploadFileToBackend = async (file: File, type: string, fileId: string): Promise<void> => {
    try {
      const formData = new FormData();
      formData.append('file', file);

      // Determine the correct endpoint
      const endpoint = type === 'input' ? '/upload/input' : '/upload/reference';
      
      const response = await fetch(`${API_BASE_URL}${endpoint}`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Upload failed' }));
        throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
      }

      const uploadResponse: UploadResponse = await response.json();

      if (uploadResponse.success) {
        // Update the file status to completed with the file path
        if (type === 'input') {
          setInputFile(prev => prev && prev.id === fileId ? {
            ...prev,
            status: 'completed',
            file_path: uploadResponse.file_path,
            file_id: uploadResponse.file_id,
            original_filename: uploadResponse.original_filename,
            file_size: uploadResponse.file_size
          } : prev);
        } else if (type === 'reference') {
          setReferenceFiles(prev => 
            prev.map(f => f.id === fileId ? {
              ...f,
              status: 'completed',
              file_path: uploadResponse.file_path,
              file_id: uploadResponse.file_id,
              original_filename: uploadResponse.original_filename,
              file_size: uploadResponse.file_size
            } : f)
          );
        }
      } else {
        throw new Error(uploadResponse.message || 'Upload failed');
      }
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Upload failed';
      console.error(`❌ Upload error for ${file.name}:`, error);
      
      // Update file status to error
      if (type === 'input') {
        setInputFile(prev => prev && prev.id === fileId ? {
          ...prev,
          status: 'error',
          error_message: errorMessage
        } : prev);
      } else if (type === 'reference') {
        setReferenceFiles(prev => 
          prev.map(f => f.id === fileId ? {
            ...f,
            status: 'error',
            error_message: errorMessage
          } : f)
        );
      }
    }
  };

  const removeFile = async (type: string, id: string): Promise<void> => {
    try {
      let fileToRemove: FileWithStatus | null | undefined;
      
      if (type === 'input') {
        fileToRemove = inputFile;
      } else if (type === 'reference') {
        fileToRemove = referenceFiles.find(f => f.id === id);
      }

      // Set status to removing to show visual feedback
      if (type === 'input' && inputFile) {
        setInputFile(prev => prev ? { ...prev, status: 'removing' } : prev);
      } else if (type === 'reference') {
        setReferenceFiles(prev => 
          prev.map(f => f.id === id ? { ...f, status: 'removing' } : f)
        );
      }

      // If file exists on server, delete it
      if (fileToRemove && fileToRemove.file_path && (fileToRemove.status === 'completed' || fileToRemove.status === 'removing')) {
        const deleteResponse = await fetch(`${API_BASE_URL}/files/delete?file_path=${encodeURIComponent(fileToRemove.file_path)}`, {
          method: 'DELETE',
        });

        if (!deleteResponse.ok) {
          const errorData = await deleteResponse.json().catch(() => ({ detail: 'Delete failed' }));
          throw new Error(errorData.detail || 'Failed to delete file');
        }
      }

      // Remove from state on success
      if (type === 'input') {
        setInputFile(null);
      } else if (type === 'reference') {
        setReferenceFiles(prev => prev.filter(f => f.id !== id));
      }
    } catch (error) {
      console.error('Error removing file:', error);
      
      // Reset status to completed on error
      if (type === 'input' && inputFile) {
        setInputFile(prev => prev ? { ...prev, status: 'completed', error_message: 'Failed to remove file' } : prev);
      } else if (type === 'reference') {
        setReferenceFiles(prev => 
          prev.map(f => f.id === id ? { ...f, status: 'error', error_message: 'Failed to remove file' } : f)
        );
      }
      
      setError(error instanceof Error ? error.message : 'Failed to remove file');
    }
  };

  const runValidation = async (): Promise<void> => {
    if (!inputFile || inputFile.status !== 'completed' || !inputFile.file_path) {
      setError('Please upload an input document');
      return;
    }

    const completedReferenceFiles = referenceFiles.filter(f => f.status === 'completed' && f.file_path);
    if (completedReferenceFiles.length === 0) {
      setError('Please upload at least one reference document');
      return;
    }

    setIsRunning(true);
    setError(null);
    setResults(null);

    try {
      const formData = new FormData();
      formData.append('input_file_path', inputFile.file_path);
      formData.append('reference_file_path', completedReferenceFiles[0].file_path!);
      formData.append('instructions', instructions);

      const response = await fetch(`${API_BASE_URL}/validate-uploaded`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Validation failed' }));
        throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
      }

      const data: ValidationResult = await response.json();
      setResults(data);
      
      // Auto-expand all categories initially
      if (data.findings) {
        const categories = new Set(data.findings.map(f => f.category));
        setExpandedCategories(categories);
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'An unknown error occurred';
      setError(errorMessage);
      console.error('Validation error:', err);
    } finally {
      setIsRunning(false);
    }
  };

  const getStatusIcon = (status: 'uploading' | 'completed' | 'error' | 'removing') => {
    switch (status) {
      case 'uploading':
        return <Loader className="h-4 w-4 text-blue-500 animate-spin" />;
      case 'removing':
        return <Loader className="h-4 w-4 text-red-500 animate-spin" />;
      case 'completed':
        return <CheckCircle className="h-4 w-4 text-emerald-500" />;
      case 'error':
        return <AlertCircle className="h-4 w-4 text-red-500" />;
    }
  };

  const getStatusColor = (status: 'uploading' | 'completed' | 'error' | 'removing') => {
    switch (status) {
      case 'uploading':
        return 'border-blue-200 bg-blue-50';
      case 'removing':
        return 'border-red-200 bg-red-50';
      case 'completed':
        return 'border-emerald-200 bg-emerald-50';
      case 'error':
        return 'border-red-200 bg-red-50';
    }
  };

  const getStatusText = (fileWithStatus: FileWithStatus) => {
    switch (fileWithStatus.status) {
      case 'uploading':
        return 'Uploading...';
      case 'removing':
        return 'Removing...';
      case 'completed':
        return 'Ready';
      case 'error':
        return fileWithStatus.error_message || 'Upload failed';
    }
  };

  const toggleCategory = (category: string) => {
    const newExpanded = new Set(expandedCategories);
    if (newExpanded.has(category)) {
      newExpanded.delete(category);
    } else {
      newExpanded.add(category);
    }
    setExpandedCategories(newExpanded);
  };

  // Group and filter findings
  const groupedFindings = results?.findings.reduce((acc, finding) => {
    if (!acc[finding.category]) {
      acc[finding.category] = { critical: [], warning: [], info: [] };
    }
    acc[finding.category][finding.severity].push(finding);
    return acc;
  }, {} as Record<string, Record<string, Finding[]>>) || {};

  // Filter by selected severity
  const filteredFindings = selectedSeverity === 'all' 
    ? groupedFindings 
    : Object.fromEntries(
        Object.entries(groupedFindings).map(([category, severities]) => [
          category,
          { 
            critical: selectedSeverity === 'critical' ? severities.critical : [],
            warning: selectedSeverity === 'warning' ? severities.warning : [],
            info: selectedSeverity === 'info' ? severities.info : []
          }
        ])
      );

  // Summary statistics
  const findingSummary = results?.findings.reduce((acc, finding) => {
    acc[finding.severity]++;
    return acc;
  }, { critical: 0, warning: 0, info: 0 }) || { critical: 0, warning: 0, info: 0 };

  const FileUploadSection: React.FC<{
    title: string;
    files: FileWithStatus[];
    onFileChange: (file: File, type: string) => void;
    type: string;
    multiple?: boolean;
  }> = ({ title, files, onFileChange, type, multiple = false }) => (
    <div className="bg-white rounded-xl p-6 border-2 border-dashed border-slate-300 hover:border-blue-400 transition-all duration-300 hover:bg-blue-50/30">
      <div className="text-center">
        <Upload className="mx-auto h-12 w-12 text-slate-400 hover:text-blue-500 transition-colors" />
        <div className="mt-4">
          <label htmlFor={`file-${type}`} className="cursor-pointer">
            <span className="mt-2 block text-lg font-semibold text-slate-900 hover:text-blue-600 transition-colors">
              {title}
            </span>
            <span className="mt-1 block text-sm text-slate-600">
              Click to upload or drag and drop{multiple ? ' (multiple files supported)' : ''}
            </span>
            <span className="mt-1 block text-xs text-slate-500">
              Supports: .txt, .pdf, .doc, .docx
            </span>
          </label>
          <input
            id={`file-${type}`}
            type="file"
            className="hidden"
            accept=".txt,.pdf,.doc,.docx"
            multiple={multiple}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
              const selectedFiles = Array.from(e.target.files || []);
              selectedFiles.forEach(file => onFileChange(file, type));
              e.target.value = '';
            }}
          />
        </div>
      </div>
      
      {files.length > 0 && (
        <div className="mt-6 space-y-3">
          {files.map((fileWithStatus) => (
            <div 
              key={fileWithStatus.id} 
              className={`flex items-center justify-between p-4 rounded-lg border transition-all duration-300 ${getStatusColor(fileWithStatus.status)} ${
                fileWithStatus.status === 'completed' ? 'shadow-sm' : ''
              }`}
            >
              <div className="flex items-center flex-1">
                <File className="h-5 w-5 text-slate-600 mr-3 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-slate-900 truncate">{fileWithStatus.original_filename}</span>
                    {getStatusIcon(fileWithStatus.status)}
                  </div>
                  <div className="flex items-center gap-3 mt-1">
                    <span className="text-xs text-slate-600 bg-slate-100 px-2 py-1 rounded">
                      {(fileWithStatus.file_size / 1024).toFixed(1)} KB
                    </span>
                    <span className={`text-xs font-medium px-2 py-1 rounded-full ${
                      fileWithStatus.status === 'uploading' ? 'bg-blue-100 text-blue-700' : 
                      fileWithStatus.status === 'removing' ? 'bg-red-100 text-red-700' :
                      fileWithStatus.status === 'error' ? 'bg-red-100 text-red-700' : 
                      'bg-emerald-100 text-emerald-700'
                    }`}>
                      {getStatusText(fileWithStatus)}
                    </span>
                  </div>
                </div>
              </div>
              <button
                onClick={() => removeFile(type, fileWithStatus.id)}
                className="text-red-500 hover:text-red-700 text-sm font-medium px-3 py-1 rounded-md hover:bg-red-100 transition-all duration-200 flex-shrink-0 ml-3 disabled:opacity-50 disabled:cursor-not-allowed"
                disabled={fileWithStatus.status === 'uploading' || fileWithStatus.status === 'removing'}
              >
                {fileWithStatus.status === 'removing' ? 'Removing...' : 'Remove'}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  const hasCompletedInputFile = inputFile?.status === 'completed' && inputFile.file_path;
  const completedReferenceFiles = referenceFiles.filter(f => f.status === 'completed' && f.file_path);
  const hasCompletedReferenceFiles = completedReferenceFiles.length > 0;
  const canRunValidation = hasCompletedInputFile && hasCompletedReferenceFiles;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50">
      <div className="max-w-6xl mx-auto p-6">
        <div className="mb-8 text-center pt-4">
          <h1 className="text-4xl font-bold bg-gradient-to-r from-blue-600 to-purple-600 bg-clip-text text-transparent mb-3">
            Document Validator
          </h1>
          <p className="text-slate-600 text-lg">Upload documents and validate them against reference materials</p>
        </div>

        {/* File Upload Sections */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
          <FileUploadSection
            title="Input Document"
            files={inputFile ? [inputFile] : []}
            onFileChange={handleFileUpload}
            type="input"
            multiple={false}
          />
          <FileUploadSection
            title="Reference Documents"
            files={referenceFiles}
            onFileChange={handleFileUpload}
            type="reference"
            multiple={true}
          />
        </div>

        {/* Instructions Section */}
        <div className="mb-8">
          <div className="bg-white rounded-xl p-6 border border-slate-200 shadow-sm">
            <label htmlFor="instructions" className="block text-lg font-semibold text-slate-900 mb-3">
              🎯 Validation Instructions <span className="text-sm text-slate-500 font-normal">(Optional)</span>
            </label>
            <textarea
              id="instructions"
              rows={4}
              className="w-full px-4 py-3 bg-slate-50 border border-slate-300 rounded-lg shadow-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 text-slate-900 placeholder-slate-500 transition-all duration-200"
              placeholder="Enter specific instructions for validation (e.g., 'Check for compliance violations', 'Validate security requirements', etc.) or leave blank for general validation"
              value={instructions}
              onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setInstructions(e.target.value)}
            />
          </div>
        </div>

        {/* Run Button */}
        <div className="mb-8 text-center">
          <button
            onClick={runValidation}
            disabled={isRunning || !canRunValidation}
            className="inline-flex items-center px-8 py-4 border border-transparent text-lg font-semibold rounded-xl shadow-lg text-white bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transform transition-all duration-200 hover:scale-105 disabled:hover:scale-100"
          >
            {isRunning ? (
              <>
                <Loader className="animate-spin -ml-1 mr-3 h-6 w-6 text-white" />
                Running Validation...
              </>
            ) : (
              <>
                <Play className="-ml-1 mr-3 h-6 w-6" />
                Run Validation
              </>
            )}
          </button>
          {!canRunValidation && (
            <p className="mt-2 text-sm text-slate-500">
              {!hasCompletedInputFile && "Upload an input document • "}
              {!hasCompletedReferenceFiles && "Upload reference documents"}
            </p>
          )}
        </div>

        {/* Error Display */}
        {error && (
          <div className="mb-6 rounded-xl bg-red-50 p-4 border border-red-200">
            <div className="flex">
              <AlertCircle className="h-5 w-5 text-red-500" />
              <div className="ml-3">
                <h3 className="text-sm font-medium text-red-800">Error</h3>
                <div className="mt-2 text-sm text-red-700">{error}</div>
              </div>
            </div>
          </div>
        )}

        {/* Results Display */}
        {results && (
          <div className="bg-white rounded-xl p-6 border border-slate-200 shadow-sm">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-2xl font-bold text-slate-900 flex items-center">
                📊 Validation Results
              </h2>
              
              {/* Severity Filter */}
              <div className="flex items-center gap-2">
                <Filter className="h-4 w-4 text-slate-500" />
                <select
                  value={selectedSeverity}
                  onChange={(e) => setSelectedSeverity(e.target.value)}
                  className="px-3 py-1 border border-slate-300 rounded-md text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                >
                  <option value="all">All Severities</option>
                  <option value="critical">Critical Only</option>
                  <option value="warning">Warning Only</option>
                  <option value="info">Info Only</option>
                </select>
              </div>
            </div>
            
            {/* Improved Summary Statistics */}
            <div className="grid grid-cols-3 gap-4 mb-8">
              {Object.entries(severityConfig).map(([severity, config]) => (
                <div key={severity} className={`${config.bgColor} border ${config.borderColor} rounded-lg p-4 text-center`}>
                  <div className="flex items-center justify-center mb-2">
                    <config.icon className={`h-6 w-6 ${config.color}`} />
                  </div>
                  <div className={`text-2xl font-bold ${config.color}`}>
                    {findingSummary[severity as keyof typeof findingSummary]}
                  </div>
                  <div className="text-sm font-medium text-slate-700">{config.label}</div>
                  <div className="text-xs text-slate-500">{config.description}</div>
                </div>
              ))}
            </div>

            {results.success ? (
              <div className="space-y-6">
                <div className="flex items-center text-emerald-600 bg-emerald-50 p-4 rounded-lg border border-emerald-200">
                  <CheckCircle className="h-6 w-6 mr-3" />
                  <span className="font-medium text-lg">{results.message}</span>
                </div>
                
                {results.findings && results.findings.length > 0 ? (
                  <div>
                    {/* Findings grouped by category */}
                    {Object.entries(filteredFindings).map(([category, findings]) => {
                      const categoryHasFindings = findings.critical.length > 0 || findings.warning.length > 0 || findings.info.length > 0;
                      if (!categoryHasFindings) return null;
                      
                      return (
                        <div key={category} className="mb-6">
                          <button
                            onClick={() => toggleCategory(category)}
                            className="w-full flex items-center justify-between p-4 bg-slate-50 hover:bg-slate-100 rounded-lg border border-slate-200 transition-colors duration-200"
                          >
                            <div className="flex items-center">
                              <Shield className="h-5 w-5 text-slate-600 mr-3" />
                              <h4 className="text-lg font-semibold text-slate-900">{category}</h4>
                              <span className="ml-3 text-sm text-slate-500">
                                ({findings.critical.length + findings.warning.length + findings.info.length} findings)
                              </span>
                            </div>
                            {expandedCategories.has(category) ? 
                              <ChevronUp className="h-5 w-5 text-slate-500" /> :
                              <ChevronDown className="h-5 w-5 text-slate-500" />
                            }
                          </button>
                          
                          {expandedCategories.has(category) && (
                            <div className="mt-4 space-y-3">
                              {(['critical', 'warning', 'info'] as const).map(severity => (
                                findings[severity].map((finding, index) => {
                                  const config = severityConfig[severity];
                                  return (
                                    <div 
                                      key={`${severity}-${index}`} 
                                      className={`${config.bgColor} border ${config.borderColor} rounded-lg p-4 ml-4`}
                                    >
                                      <div className="flex items-start">
                                        <config.icon className={`h-5 w-5 ${config.color} mr-3 mt-1 flex-shrink-0`} />
                                        <div className="flex-1">
                                          <div className="flex items-center mb-2">
                                            <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${config.badgeColor}`}>
                                              {config.label}
                                            </span>
                                          </div>
                                          <div className="prose prose-slate max-w-none text-slate-700 leading-relaxed">
                                            <ReactMarkdown 
                                              components={{
                                                h1: (props: any) => <h1 className="text-lg font-bold text-slate-900 mb-2" {...props} />,
                                                h2: (props: any) => <h2 className="text-base font-semibold text-slate-800 mb-2 mt-3" {...props} />,
                                                h3: (props: any) => <h3 className="text-sm font-semibold text-slate-700 mb-1 mt-2" {...props} />,
                                                p: (props: any) => <p className="mb-2 text-slate-700" {...props} />,
                                                ul: (props: any) => <ul className="list-disc list-inside mb-2 text-slate-700" {...props} />,
                                                ol: (props: any) => <ol className="list-decimal list-inside mb-2 text-slate-700" {...props} />,
                                                li: (props: any) => <li className="mb-1 text-slate-700" {...props} />,
                                                strong: (props: any) => <strong className="font-semibold text-slate-900" {...props} />,
                                                em: (props: any) => <em className="italic text-slate-600" {...props} />,
                                                code: (props: any) => <code className="bg-slate-200 px-2 py-1 rounded text-slate-800 font-mono text-sm" {...props} />,
                                                blockquote: (props: any) => <blockquote className="border-l-4 border-slate-300 pl-4 italic text-slate-600 my-3" {...props} />
                                              }}
                                            >
                                              {finding.content}
                                            </ReactMarkdown>
                                          </div>
                                        </div>
                                      </div>
                                    </div>
                                  );
                                })
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="bg-emerald-50 border border-emerald-200 p-4 rounded-lg">
                    <div className="text-emerald-700">✅ No violations or issues found.</div>
                  </div>
                )}
              </div>
            ) : (
              <div className="rounded-lg bg-red-50 p-4 border border-red-200">
                <div className="flex">
                  <AlertCircle className="h-5 w-5 text-red-500" />
                  <div className="ml-3">
                    <h3 className="text-sm font-medium text-red-800">Validation Failed</h3>
                    <div className="mt-2 text-sm text-red-700">{results.message}</div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default DocumentValidator;