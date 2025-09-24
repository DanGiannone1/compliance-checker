"use client";

import React, { useState, useEffect } from 'react';
import { Upload, File, Play, CheckCircle, AlertCircle, Loader } from 'lucide-react';
import ReactMarkdown from 'react-markdown';

// Types
interface ValidationResult {
  success: boolean;
  message: string;
  raw_output?: string;
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
  file?: File;
  status: 'uploading' | 'completed' | 'error' | 'removing';
  id: string;
  file_path?: string;
  file_id?: string;
  error_message?: string;
  original_filename: string;
  file_size: number;
  upload_date?: string;
  is_existing?: boolean;
}

const API_BASE_URL = 'http://localhost:8000';

const DocumentValidator: React.FC = () => {
  const [inputFile, setInputFile] = useState<FileWithStatus | null>(null);
  const [referenceFiles, setReferenceFiles] = useState<FileWithStatus[]>([]);
  const [instructions, setInstructions] = useState<string>('');
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [results, setResults] = useState<ValidationResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const generateFileId = () => Math.random().toString(36).substr(2, 9);

  useEffect(() => {
    loadExistingFiles();
  }, []);

  const loadExistingFiles = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/files/all`);
      if (response.ok) {
        const data = await response.json();

        const convertToFileWithStatus = (fileInfo: FileInfo): FileWithStatus => ({
          status: 'completed' as const,
          id: generateFileId(),
          file_path: fileInfo.file_path,
          original_filename: fileInfo.original_filename,
          file_size: fileInfo.file_size,
          upload_date: fileInfo.upload_date,
          is_existing: true
        });

        if (data.input_files && data.input_files.length > 0) {
          // Show the most recent input first
          const latest = [...data.input_files].sort((a: FileInfo, b: FileInfo) =>
            (new Date(a.upload_date).getTime()) - (new Date(b.upload_date).getTime())
          ).slice(-1)[0];
          setInputFile(convertToFileWithStatus(latest));
        } else {
          setInputFile(null);
        }

        if (data.reference_files && data.reference_files.length > 0) {
          const refs = data.reference_files.map((fi: FileInfo) => convertToFileWithStatus(fi));
          setReferenceFiles(refs);
        } else {
          setReferenceFiles([]);
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

    uploadFileToBackend(file, type, fileId);
  };

  const uploadFileToBackend = async (file: File, type: string, fileId: string): Promise<void> => {
    try {
      const formData = new FormData();
      formData.append('file', file);

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

      if (type === 'input' && inputFile) {
        setInputFile(prev => prev ? { ...prev, status: 'removing' } : prev);
      } else if (type === 'reference') {
        setReferenceFiles(prev =>
          prev.map(f => f.id === id ? { ...f, status: 'removing' } : f)
        );
      }

      if (fileToRemove && fileToRemove.file_path && (fileToRemove.status === 'completed' || fileToRemove.status === 'removing')) {
        const deleteResponse = await fetch(`${API_BASE_URL}/files/delete?file_path=${encodeURIComponent(fileToRemove.file_path)}`, {
          method: 'DELETE',
        });

        if (!deleteResponse.ok) {
          const errorData = await deleteResponse.json().catch(() => ({ detail: 'Delete failed' }));
          throw new Error(errorData.detail || 'Failed to delete file');
        }
      }

      if (type === 'input') {
        setInputFile(null);
      } else if (type === 'reference') {
        setReferenceFiles(prev => prev.filter(f => f.id !== id));
      }
    } catch (error) {
      console.error('Error removing file:', error);

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
    setIsRunning(true);
    setError(null);
    setResults(null);

    try {
      // Backend auto-discovers latest input + all references; we only pass optional instructions
      const formData = new FormData();
      formData.append('instructions', instructions);

      const response = await fetch(`${API_BASE_URL}/validate`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Validation failed' }));
        throw new Error(errorData.detail?.message || errorData.detail || `HTTP error! status: ${response.status}`);
      }

      const data: ValidationResult = await response.json();
      setResults(data);
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
        return <CheckCircle className="h-4 w-4 text-green-500" />;
      case 'error':
        return <AlertCircle className="h-4 w-4 text-red-500" />;
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

  const FileUploadSection: React.FC<{
    title: string;
    files: FileWithStatus[];
    onFileChange: (file: File, type: string) => void;
    type: string;
    multiple?: boolean;
  }> = ({ title, files, onFileChange, type, multiple = false }) => (
    <div className="border-2 border-dashed border-gray-300 rounded-lg p-6 hover:border-gray-400 transition-colors">
      <div className="text-center">
        <Upload className="mx-auto h-8 w-8 text-gray-400" />
        <div className="mt-2">
          <label htmlFor={`file-${type}`} className="cursor-pointer">
            <span className="block text-sm font-medium text-gray-700">{title}</span>
            <span className="block text-xs text-gray-500 mt-1">
              Click to upload{multiple ? ' (multiple files supported)' : ''}
            </span>
          </label>
          <input
            id={`file-${type}`}
            type="file"
            className="hidden"
            accept=".pdf,application/pdf"
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
        <div className="mt-4 space-y-2">
          {files.map((fileWithStatus) => (
            <div key={fileWithStatus.id} className="flex items-center justify-between p-3 bg-gray-50 rounded">
              <div className="flex items-center flex-1">
                <File className="h-4 w-4 text-gray-500 mr-2" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-gray-900 truncate">{fileWithStatus.original_filename}</span>
                    {getStatusIcon(fileWithStatus.status)}
                  </div>
                  <span className="text-xs text-gray-500">{getStatusText(fileWithStatus)}</span>
                </div>
              </div>
              <button
                onClick={() => removeFile(type, fileWithStatus.id)}
                className="text-red-600 hover:text-red-800 text-sm ml-2 disabled:opacity-50"
                disabled={fileWithStatus.status === 'uploading' || fileWithStatus.status === 'removing'}
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-bold text-gray-900 mb-2">Document Validator</h1>
          <p className="text-gray-600">Upload PDFs. Click validate. Backend auto-selects latest input & all references.</p>
        </div>

        {/* File Upload Sections */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
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

        {/* Instructions Section (optional) */}
        <div className="mb-6">
          <div className="bg-white rounded-lg p-4 border border-gray-200">
            <label htmlFor="instructions" className="block text-sm font-medium text-gray-700 mb-2">
              Validation Instructions (Optional)
            </label>
            <textarea
              id="instructions"
              rows={3}
              className="w-full px-3 py-2 border border-gray-300 rounded focus:ring-blue-500 focus:border-blue-500"
              placeholder="Enter specific instructions for validation..."
              value={instructions}
              onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setInstructions(e.target.value)}
            />
          </div>
        </div>

        {/* Run Button */}
        <div className="mb-6 text-center">
          <button
            onClick={runValidation}
            disabled={isRunning}
            className="inline-flex items-center px-6 py-3 border border-transparent text-base font-medium rounded-md shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isRunning ? (
              <>
                <Loader className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" />
                Running Validation...
              </>
            ) : (
              <>
                <Play className="-ml-1 mr-3 h-5 w-5" />
                Validate
              </>
            )}
          </button>
        </div>

        {/* Error Display */}
        {error && (
          <div className="mb-6 rounded-md bg-red-50 p-4 border border-red-200">
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
          <div className="bg-white rounded-lg p-6 border border-gray-200">
            <h2 className="text-xl font-bold text-gray-900 mb-4">Validation Results</h2>

            {results.success ? (
              <div>
                <div className="flex items-center text-green-600 bg-green-50 p-3 rounded-md border border-green-200 mb-4">
                  <CheckCircle className="h-5 w-5 mr-2" />
                  <span className="font-medium">{results.message}</span>
                </div>

                {results.raw_output && (
                  <div className="prose prose-slate max-w-none prose-headings:text-gray-900 prose-p:text-gray-800 prose-li:text-gray-800 prose-strong:text-gray-900">
                    <ReactMarkdown
                      components={{
                        h1: (props: any) => <h1 className="text-xl font-bold text-gray-900 mb-3 mt-6 first:mt-0" {...props} />,
                        h2: (props: any) => <h2 className="text-lg font-semibold text-gray-900 mb-2 mt-5 first:mt-0" {...props} />,
                        h3: (props: any) => <h3 className="text-lg font-bold text-gray-900 mb-2 mt-4 first:mt-0" {...props} />,
                        p: (props: any) => <p className="mb-3 text-gray-800 leading-relaxed" {...props} />,
                        ul: (props: any) => <ul className="list-disc list-inside mb-3 space-y-1" {...props} />,
                        ol: (props: any) => <ol className="list-decimal list-inside mb-3 space-y-1" {...props} />,
                        li: (props: any) => <li className="text-gray-800 leading-relaxed" {...props} />,
                        strong: (props: any) => <strong className="font-semibold text-gray-900" {...props} />,
                        em: (props: any) => <em className="italic text-gray-700" {...props} />,
                        code: (props: any) => <code className="bg-gray-100 px-2 py-1 rounded text-gray-900 font-mono text-sm" {...props} />,
                        blockquote: (props: any) => <blockquote className="border-l-4 border-gray-300 pl-4 italic text-gray-700 my-4 bg-gray-50 py-2" {...props} />,
                        hr: (props: any) => <hr className="my-6 border-gray-300" {...props} />
                      }}
                    >
                      {results.raw_output}
                    </ReactMarkdown>
                  </div>
                )}
              </div>
            ) : (
              <div className="rounded-md bg-red-50 p-4 border border-red-200">
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
