var socket = io();

// Recording command configuration
const recordingCommands = {
    list_recordings: {
        label: 'List Recordings',
        type: 'list_recordings',
        description: 'List available recordings on selected modules',
        needsFollowup: false
        // Expected response: {status: 200, recordings: [{filename: "rec/exp1_060127698.mp4", size: 1024000, created: "2024-01-15T10:30:00Z", duration: 125.5}]}
    },
    export_recordings: {
        label: 'Export Recordings',
        type: 'export_recordings',
        description: 'Export recordings from selected modules',
        needsFollowup: true,
        // Expected response: {status: 200, exported_files: ["{destination}/path/to/exported/file.mp4"], message: "Export completed successfully"}
        followupQuestions: [
            {
                id: 'destination',
                label: 'Export Destination',
                type: 'select',
                options: [
                    { value: 'nas', label: 'NAS Storage' },
                    { value: 'controller', label: 'Controller Storage' }
                ],
                required: true
            }
        ]
    },
    clear_recordings: {
        label: 'Clear Recordings',
        type: 'clear_recordings',
        description: 'Clear all recordings on selected modules',
        needsFollowup: true,
        // Expected response: {status: 200, deleted_count: 5, message: "5 recordings deleted successfully"}
        followupQuestions: [
            {
                id: 'confirmation',
                label: 'Are you sure you want to delete ALL recordings on the module?',
                type: 'confirmation',
                message: 'This action cannot be undone. All recordings on the selected modules will be permanently deleted.',
                required: true
            }
        ]
    }
};

// Generate recording command buttons
function generateRecordingCommandButtons() {
    const container = document.getElementById('recording-commands-content');
    container.innerHTML = ''; // Clear existing buttons

    Object.entries(recordingCommands).forEach(([id, command]) => {
        const button = document.createElement('button');
        button.id = id;
        button.className = 'command-button';
        button.textContent = command.label;
        button.title = command.description;
        
        button.addEventListener('click', () => {
            currentCommand = command.type;
            dialog.style.display = 'flex';
        });
        
        container.appendChild(button);
    });
}

// Module selection dialog
const dialog = document.getElementById('module-selection-dialog');
const moduleOptions = document.getElementById('module-options');
const closeButton = document.querySelector('.dialog-close');
let currentCommand = null;
let selectedModuleId = null;

// Follow-up dialog elements
const followupDialog = document.getElementById('followup-dialog');
const followupTitle = document.getElementById('followup-title');
const followupContent = document.getElementById('followup-content');
const followupConfirm = document.getElementById('followup-confirm');
const followupCancel = document.getElementById('followup-cancel');

// Add null checks for critical elements
if (!dialog) {
    console.error('Module selection dialog not found');
}
if (!moduleOptions) {
    console.error('Module options container not found');
}
if (!closeButton) {
    console.error('Dialog close button not found');
}
if (!followupDialog) {
    console.error('Follow-up dialog not found');
}
if (!followupTitle) {
    console.error('Follow-up title not found');
}
if (!followupContent) {
    console.error('Follow-up content not found');
}
if (!followupConfirm) {
    console.error('Follow-up confirm button not found');
}
if (!followupCancel) {
    console.error('Follow-up cancel button not found');
}

// Command Button Event Listeners
if (closeButton) {
    closeButton.addEventListener('click', () => {
        if (dialog) dialog.style.display = 'none';
        currentCommand = null;
        selectedModuleId = null;
        window.currentExportFilename = null;
        window.currentExportExperimentName = null; // Clear stored experiment name
    });
}

// Follow-up dialog event listeners
if (followupCancel) {
    followupCancel.addEventListener('click', () => {
        if (followupDialog) followupDialog.style.display = 'none';
        currentCommand = null;
        selectedModuleId = null;
        window.currentExportFilename = null;
        window.currentExportExperimentName = null; // Clear stored experiment name
    });
}

if (followupConfirm) {
    followupConfirm.addEventListener('click', () => {
        const formData = collectFollowupData();
        if (formData) {
            sendCommandWithParams(selectedModuleId, formData);
            if (followupDialog) followupDialog.style.display = 'none';
        }
    });
}

// Generate follow-up form
function generateFollowupForm(commandType) {
    const command = recordingCommands[commandType];
    if (!command || !command.needsFollowup) return '';

    followupTitle.textContent = `Configure ${command.label}`;
    let formHTML = '';

    command.followupQuestions.forEach(question => {
        formHTML += `<div class="form-group">`;
        formHTML += `<label for="${question.id}">${question.label}</label>`;

        let defaultValue = question.default || '';

        switch (question.type) {
            case 'text':
                formHTML += `<input type="text" id="${question.id}" placeholder="${question.placeholder || ''}" value="${defaultValue}" ${question.required ? 'required' : ''}>`;
                break;
            case 'number':
                formHTML += `<input type="number" id="${question.id}" placeholder="${question.placeholder || ''}" min="${question.min || ''}" max="${question.max || ''}" value="${defaultValue}" ${question.required ? 'required' : ''}>`;
                break;
            case 'select':
                formHTML += `<select id="${question.id}" ${question.required ? 'required' : ''}>`;
                question.options.forEach(option => {
                    const selected = option.value === defaultValue ? 'selected' : '';
                    formHTML += `<option value="${option.value}" ${selected}>${option.label}</option>`;
                });
                formHTML += `</select>`;
                break;
            case 'checkbox':
                const checked = defaultValue ? 'checked' : '';
                formHTML += `<input type="checkbox" id="${question.id}" ${checked}>`;
                break;
            case 'confirmation':
                formHTML += `<div class="confirmation-message">${question.message}</div>`;
                formHTML += `<input type="checkbox" id="${question.id}" ${question.required ? 'required' : ''}> I understand and want to proceed`;
                break;
        }

        formHTML += `</div>`;
    });

    return formHTML;
}

// Collect data from follow-up form
function collectFollowupData() {
    const command = recordingCommands[currentCommand];
    if (!command || !command.needsFollowup) return {};

    const formData = {};
    let isValid = true;

    command.followupQuestions.forEach(question => {
        const element = document.getElementById(question.id);
        if (!element) return;

        let value;
        switch (question.type) {
            case 'text':
            case 'number':
                value = element.value.trim();
                break;
            case 'select':
                value = element.value;
                break;
            case 'checkbox':
                value = element.checked;
                break;
            case 'confirmation':
                value = element.checked;
                break;
        }

        // Validation
        if (question.required && (value === '' || value === false || value === null)) {
            showError(`${question.label} is required`);
            isValid = false;
            return;
        }

        if (question.validation && value !== '') {
            const error = question.validation(value);
            if (error) {
                showError(error);
                isValid = false;
                return;
            }
        }

        formData[question.id] = value;
        
        // Auto-save experiment name if this is the experiment_name field
        if (question.id === 'experiment_name' && value.trim()) {
            console.log('Auto-saving experiment name from follow-up dialog:', value);
            socket.emit('save_experiment_name', {experiment_name: value.trim()});
        }
    });

    return isValid ? formData : null;
}

// Show error message
function showError(message) {
    alert(message);
}

// Send command with parameters
function sendCommandWithParams(moduleId, params) {
    const command = {
        type: currentCommand,
        module_id: moduleId,
        params: params
    };

    // If we have a stored filename (from individual export button), add it to params
    if (window.currentExportFilename && currentCommand === 'export_recordings') {
        command.params.filename = window.currentExportFilename;
        
        // Use stored experiment name if available (from "Export All" button)
        if (window.currentExportExperimentName) {
            command.params.experiment_name = window.currentExportExperimentName;
            window.currentExportExperimentName = null; // Clear it
        } else {
            // Extract experiment name from the filename(s)
            let experimentName = "unknown";
            if (window.currentExportFilename.includes(',')) {
                // Multiple files - extract from first filename
                const firstFilename = window.currentExportFilename.split(',')[0];
                experimentName = extractExperimentName(firstFilename);
            } else {
                // Single file
                experimentName = extractExperimentName(window.currentExportFilename);
            }
            command.params.experiment_name = experimentName;
        }
        
        // Clear the stored filename
        window.currentExportFilename = null;
    }

    console.log('Sending command with params:', command);
    socket.emit('command', command);
    currentCommand = null;
    selectedModuleId = null;
}

// Update the module selection dialog options
function updateModuleOptions(modules) {
    moduleOptions.innerHTML = '';
    modules.forEach(module => {
        const button = document.createElement('button');
        button.className = 'dialog-option';
        button.textContent = `${module.type} (${module.id})`;
        button.dataset.moduleId = module.id;
        button.addEventListener('click', () => {
            selectedModuleId = module.id;
            handleModuleSelection();
        });
        moduleOptions.appendChild(button);
    });
}

// Handle module selection
function handleModuleSelection() {
    dialog.style.display = 'none';
    
    const command = recordingCommands[currentCommand];
    if (command && command.needsFollowup) {
        // Show follow-up dialog
        followupContent.innerHTML = generateFollowupForm(currentCommand);
        followupDialog.style.display = 'flex';
    } else {
        // Send command immediately
        sendCommandWithParams(selectedModuleId, {});
    }
}

// Add click handler for "All Modules" option
const allModulesButton = document.querySelector('[data-module="all"]');
if (allModulesButton) {
    allModulesButton.addEventListener('click', () => {
        selectedModuleId = 'all';
        handleModuleSelection();
    });
} else {
    console.error('All modules button not found');
}

// Initialize recording command buttons
generateRecordingCommandButtons();

// When page loads, get initial data
socket.on('connect', function() {
    console.log('Connected to server');
    // Load initial module recordings
    console.log('Calling updateModuleRecordings() on connect');
    updateModuleRecordings();
    // Request current experiment name
    socket.emit('get_experiment_name');
    // Request module health status
    socket.emit('get_module_health');
});

socket.on('disconnect', function() {
    console.log('Disconnected from server');
});

socket.on('error', function(error) {
    console.error('Socket error:', error);
});

socket.on('experiment_name_update', function(data) {
    console.log('Experiment name updated:', data);
    const experimentNameElement = document.getElementById('experiment-name');
    if (experimentNameElement) {
        experimentNameElement.textContent = data.experiment_name || 'No experiment set';
    }
});

// Handle module updates for the selection dialog
socket.on('module_update', function(data) {
    console.log('Received module update:', data);
    if (data.modules) {
        updateModuleOptions(data.modules);
    }
});

// Handle command responses
socket.on('command_response', function(data) {
    console.log('Received command response:', data);
    if (data.success) {
        // Refresh recordings list after successful commands
        if (['export_recordings', 'clear_recordings'].includes(data.command_type)) {
            setTimeout(updateModuleRecordings, 1000); // Small delay to allow module to process
        }
    } else {
        alert(`Command failed: ${data.error || 'Unknown error'}`);
    }
});

// Handle recordings list response (legacy - keeping for backward compatibility)
socket.on('recordings_list', function(data) {
    console.log('Received legacy recordings list:', data);
    // This is now handled by the new module_recordings system
});

// Group recordings by experiment name
function groupRecordingsByExperiment(recordings) {
    const groups = {};
    
    recordings.forEach(recording => {
        // Extract experiment name from filename (assuming format: experiment_name_timestamp.ext)
        const experimentName = extractExperimentName(recording.filename);
        
        if (!groups[experimentName]) {
            groups[experimentName] = [];
        }
        groups[experimentName].push(recording);
    });
    
    return groups;
}

// Group exported recordings by folder structure
function groupExportedRecordingsByFolder(recordings) {
    const groups = {};
    
    recordings.forEach(recording => {
        // Extract folder path from filename (e.g., "controller/test.txt" -> "controller")
        const folderPath = extractFolderPath(recording.filename);
        
        if (!groups[folderPath]) {
            groups[folderPath] = [];
        }
        groups[folderPath].push(recording);
    });
    
    return groups;
}

// Extract folder path from filename
function extractFolderPath(filename) {
    // Split by '/' and take everything except the last part (the actual filename)
    const parts = filename.split('/');
    if (parts.length > 1) {
        // Return the folder path (everything except the filename)
        return parts.slice(0, -1).join('/');
    }
    
    // If no folder structure, return "Root"
    return "Root";
}

// Extract experiment name from filename
function extractExperimentName(filename) {
    // Remove file extension
    const nameWithoutExt = filename.replace(/\.[^/.]+$/, '');
    
    // Split by underscore and take the first part as experiment name
    const parts = nameWithoutExt.split('_');
    if (parts.length >= 2) {
        return parts[0];
    }
    
    // Fallback: return filename without extension
    return nameWithoutExt;
}

// Generate HTML for grouped recordings
function generateGroupedRecordingsHTML(recordingsByExperiment) {
    let html = '';
    
    Object.entries(recordingsByExperiment).forEach(([experimentName, recordings]) => {
        const totalSize = recordings.reduce((sum, rec) => sum + rec.size, 0);
        const recordingCount = recordings.length;
        
        html += `
            <div class="experiment-group" data-experiment="${experimentName}">
                <div class="experiment-header">
                    <div class="experiment-info" data-action="toggle-experiment" data-experiment="${experimentName}">
                        <span class="experiment-name">${experimentName}</span>
                        <span class="experiment-stats">${recordingCount} recording${recordingCount !== 1 ? 's' : ''} • ${formatFileSize(totalSize)}</span>
                    </div>
                    <div class="experiment-actions">
                        <button class="action-button export-all" title="Export All Recordings for ${experimentName}" data-action="export-all" data-experiment="${experimentName}">
                            Export All
                        </button>
                        <button class="action-button delete-all" title="Delete All Recordings for ${experimentName}" data-action="delete-all" data-experiment="${experimentName}">
                            Delete All
                        </button>
                        <div class="experiment-toggle" data-action="toggle-experiment" data-experiment="${experimentName}">
                            <span class="toggle-icon">▼</span>
                        </div>
                    </div>
                </div>
                <div class="experiment-recordings" id="recordings-${experimentName}">
                    ${recordings.map(recording => `
                        <div class="recording-item">
                            <div class="recording-info">
                                <span class="recording-name">${recording.filename}</span>
                                <span class="recording-date">${recording.created}</span>
                                <span class="recording-size">${formatFileSize(recording.size)}</span>
                            </div>
                            <div class="recording-actions">
                                <button class="action-button export" title="Export Recording" data-action="export" data-filename="${recording.filename}">
                                    Export
                                </button>
                                <button class="action-button delete" title="Delete Recording" data-action="delete" data-filename="${recording.filename}">
                                    Delete
                                </button>
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    });
    
    return html;
}

// Generate HTML for grouped exported recordings
function generateGroupedExportedRecordingsHTML(recordingsByFolder) {
    let html = '';
    
    Object.entries(recordingsByFolder).forEach(([folderPath, recordings]) => {
        const totalSize = recordings.reduce((sum, rec) => sum + rec.size, 0);
        const recordingCount = recordings.length;
        
        html += `
            <div class="experiment-group" data-exported-folder="${folderPath}">
                <div class="experiment-header">
                    <div class="experiment-info" data-action="toggle-exported-folder" data-folder="${folderPath}">
                        <span class="experiment-name">📁 ${folderPath}</span>
                        <span class="experiment-stats">${recordingCount} file${recordingCount !== 1 ? 's' : ''} • ${formatFileSize(totalSize)}</span>
                    </div>
                    <div class="experiment-actions">
                        <div class="experiment-toggle" data-action="toggle-exported-folder" data-folder="${folderPath}">
                            <span class="toggle-icon">▼</span>
                        </div>
                    </div>
                </div>
                <div class="experiment-recordings" id="exported-folder-${folderPath.replace(/[^a-zA-Z0-9]/g, '_')}">
                    ${recordings.map(recording => `
                        <div class="recording-item">
                            <div class="recording-info">
                                <span class="recording-name">${recording.filename.split('/').pop()}</span>
                                <span class="recording-date">${recording.created}</span>
                                <span class="recording-size">${formatFileSize(recording.size)}</span>
                                <span class="recording-destination">${recording.destination === 'nas' ? 'NAS' : 'Controller'}</span>
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    });
    
    return html;
}

// Toggle experiment group visibility
function toggleExperimentGroup(experimentName) {
    const recordingsDiv = document.getElementById(`recordings-${experimentName}`);
    const toggleIcon = document.querySelector(`[data-experiment="${experimentName}"] .toggle-icon`);
    
    if (recordingsDiv.style.display === 'none') {
        recordingsDiv.style.display = 'block';
        toggleIcon.textContent = '▼';
    } else {
        recordingsDiv.style.display = 'none';
        toggleIcon.textContent = '▶';
    }
}

// Toggle exported folder group visibility
function toggleExportedFolderGroup(folderPath) {
    const recordingsDiv = document.getElementById(`exported-folder-${folderPath.replace(/[^a-zA-Z0-9]/g, '_')}`);
    const toggleIcon = document.querySelector(`[data-exported-folder="${folderPath}"] .toggle-icon`);
    
    if (recordingsDiv.style.display === 'none') {
        recordingsDiv.style.display = 'block';
        toggleIcon.textContent = '▼';
    } else {
        recordingsDiv.style.display = 'none';
        toggleIcon.textContent = '▶';
    }
}

// Add event listeners for recordings using event delegation
function addRecordingsEventListeners() {
    // Use event delegation to handle all recording actions
    const recordingsContainer = document.querySelector('#module-recordings .recordings-list');
    if (!recordingsContainer) return;
    
    recordingsContainer.addEventListener('click', (event) => {
        const target = event.target;
        const action = target.dataset.action;
        
        if (!action) return;
        
        switch (action) {
            case 'export':
                const filename = target.dataset.filename;
                // Store the filename for later use
                window.currentExportFilename = filename;
                // Set the current command to export_recordings
                currentCommand = 'export_recordings';
                // Show the module selection dialog
                if (dialog) dialog.style.display = 'flex';
                break;
                
            case 'delete':
                const deleteFilename = target.dataset.filename;
                if (confirm(`Are you sure you want to delete ${deleteFilename}?`)) {
                    socket.emit('command', {
                        type: 'clear_recordings',
                        module_id: 'all',
                        params: {
                            filename: deleteFilename
                        }
                    });
                    // Refresh recordings list after a short delay
                    setTimeout(refreshRecordings, 2000);
                }
                break;
                
            case 'export-all':
                const experimentName = target.dataset.experiment;
                exportAllRecordings(experimentName);
                break;
                
            case 'delete-all':
                const deleteExperimentName = target.dataset.experiment;
                deleteAllRecordings(deleteExperimentName);
                break;
                
            case 'toggle-experiment':
                const toggleExperimentName = target.dataset.experiment;
                toggleExperimentGroup(toggleExperimentName);
                break;
        }
    });
}

// Add event listeners for exported recordings using event delegation
function addExportedRecordingsEventListeners() {
    // Use event delegation to handle exported recordings actions
    const exportedRecordingsContainer = document.querySelector('#exported-recordings .recordings-list');
    if (!exportedRecordingsContainer) return;
    
    exportedRecordingsContainer.addEventListener('click', (event) => {
        const target = event.target;
        const action = target.dataset.action;
        
        if (!action) return;
        
        switch (action) {
            case 'toggle-exported-folder':
                const folderPath = target.dataset.folder;
                toggleExportedFolderGroup(folderPath);
                break;
        }
    });
}

// Filter functionality
const experimentFilter = document.getElementById('experiment-filter');
if (experimentFilter) {
    experimentFilter.addEventListener('input', function() {
        const filterValue = this.value.toLowerCase();
        const experimentGroups = document.querySelectorAll('.experiment-group');
        
        experimentGroups.forEach(group => {
            // Check if this is an exported recordings group (has data-exported-folder attribute)
            const isExportedGroup = group.hasAttribute('data-exported-folder');
            
            if (isExportedGroup) {
                // For exported recordings, filter by folder name
                const folderName = group.dataset.exportedFolder.toLowerCase();
                const shouldShow = folderName.includes(filterValue);
                group.style.display = shouldShow ? 'block' : 'none';
            } else {
                // For module recordings, filter by experiment name
                const experimentName = group.dataset.experiment.toLowerCase();
                const shouldShow = experimentName.includes(filterValue);
                group.style.display = shouldShow ? 'block' : 'none';
            }
        });
    });
} else {
    console.error('Experiment filter not found');
}

// Clear filter button
const clearFilterButton = document.getElementById('clear-filter');
if (clearFilterButton) {
    clearFilterButton.addEventListener('click', function() {
        const experimentFilter = document.getElementById('experiment-filter');
        if (experimentFilter) experimentFilter.value = '';
        document.querySelectorAll('.experiment-group').forEach(group => {
            group.style.display = 'block';
        });
    });
} else {
    console.error('Clear filter button not found');
}

// Delete all recordings for a specific experiment
function deleteAllRecordings(experimentName) {
    const recordingCount = document.querySelectorAll(`[data-experiment="${experimentName}"] .recording-item`).length;
    
    if (confirm(`Are you sure you want to delete ALL ${recordingCount} recording${recordingCount !== 1 ? 's' : ''} for experiment "${experimentName}"?\n\nThis action cannot be undone.`)) {
        // Get all filenames for this experiment
        const filenames = [];
        document.querySelectorAll(`[data-experiment="${experimentName}"] .recording-item`).forEach(item => {
            const filename = item.querySelector('.recording-name').textContent;
            filenames.push(filename);
        });
        
        // Send single command with multiple filenames
        const filenameParam = filenames.join(',');
        socket.emit('command', {
            type: 'clear_recordings',
            module_id: 'all',
            params: {
                filename: filenameParam
            }
        });
        
        // Refresh recordings list after a short delay
        setTimeout(refreshRecordings, 2000);
    }
}

// Export all recordings for a specific experiment
function exportAllRecordings(experimentName) {
    const recordingCount = document.querySelectorAll(`[data-experiment="${experimentName}"] .recording-item`).length;
    
    // Get all filenames for this experiment
    const filenames = [];
    document.querySelectorAll(`[data-experiment="${experimentName}"] .recording-item`).forEach(item => {
        const filename = item.querySelector('.recording-name').textContent;
        filenames.push(filename);
    });
    
    // Store the filenames and experiment name for later use
    window.currentExportFilename = filenames.join(',');
    window.currentExportExperimentName = experimentName;
    // Set the current command to export_recordings
    currentCommand = 'export_recordings';
    // Show the module selection dialog
    dialog.style.display = 'flex';
}

// Handle export complete response
socket.on('export_complete', function(data) {
    if (data.success) {
        // Refresh the recordings list
        refreshRecordings();
    } else {
        alert(`Export failed: ${data.error || 'Unknown error'}`);
    }
});

// Function to refresh recordings (contextual - refreshes the active tab)
function refreshRecordings() {
    console.log('refreshRecordings() called');
    const activeTab = document.querySelector('.tab-button.active').getAttribute('data-tab');
    console.log('Refreshing recordings for active tab:', activeTab);
    
    // Update button text to show what's being refreshed
    const refreshButton = document.getElementById('refresh-recordings');
    if (refreshButton) {
        // Add loading state
        refreshButton.disabled = true;
        refreshButton.classList.add('loading');
        
        if (activeTab === 'module-recordings') {
            refreshButton.innerHTML = '🔄 Refreshing Module Recordings...';
        } else if (activeTab === 'exported-recordings') {
            refreshButton.innerHTML = '🔄 Refreshing Exported Recordings...';
        }
        
        // Reset button text and state after 2 seconds
        setTimeout(() => {
            refreshButton.innerHTML = '🔄 Refresh';
            refreshButton.disabled = false;
            refreshButton.classList.remove('loading');
        }, 2000);
    }
    
    if (activeTab === 'module-recordings') {
        console.log('Calling updateModuleRecordings()');
        updateModuleRecordings();
    } else if (activeTab === 'exported-recordings') {
        console.log('Calling loadExportedRecordings()');
        loadExportedRecordings();
    }
}

// Helper function to format file size
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// Function to load exported recordings (controller only, no module communication)
function loadExportedRecordings() {
    console.log('Loading exported recordings from controller...');
    socket.emit('get_exported_recordings');
}

// Function to clear recordings content when switching tabs
function clearRecordingsContent() {
    const moduleRecordingsList = document.querySelector('#module-recordings .recordings-list');
    const exportedRecordingsList = document.querySelector('#exported-recordings .recordings-list');
    
    if (moduleRecordingsList) {
        moduleRecordingsList.innerHTML = '<p>Loading...</p>';
    }
    if (exportedRecordingsList) {
        exportedRecordingsList.innerHTML = '<p>Loading...</p>';
    }
}

// Tab switching functionality
const tabButtons = document.querySelectorAll('.tab-button');
if (tabButtons.length > 0) {
    tabButtons.forEach(button => {
        button.addEventListener('click', function() {
            const targetTab = this.getAttribute('data-tab');
            
            // Update current active tab
            currentActiveTab = targetTab;
            
            // Clear content before switching
            clearRecordingsContent();
            
            // Hide all tabs
            document.querySelectorAll('.recordings-content').forEach(tab => {
                tab.classList.remove('active');
            });
            
            // Remove active class from all buttons
            document.querySelectorAll('.tab-button').forEach(btn => {
                btn.classList.remove('active');
            });
            
            // Show target tab
            const targetTabElement = document.getElementById(targetTab);
            if (targetTabElement) {
                targetTabElement.classList.add('active');
            } else {
                console.error(`Target tab element not found: ${targetTab}`);
            }
            this.classList.add('active');
            
            // Load appropriate data based on tab
            if (targetTab === 'module-recordings') {
                updateModuleRecordings();
                startModuleRecordingsAutoRefresh();
            } else if (targetTab === 'exported-recordings') {
                loadExportedRecordings();
                startExportedRecordingsAutoRefresh();
            }
        });
    });
} else {
    console.error('No tab buttons found');
}

// Auto-refresh system - only refresh the active tab
let moduleRecordingsInterval = null;
let exportedRecordingsInterval = null;
let currentActiveTab = 'module-recordings'; // Track which tab is currently active

function startModuleRecordingsAutoRefresh() {
    // Clear any existing intervals
    if (moduleRecordingsInterval) {
        clearInterval(moduleRecordingsInterval);
    }
    if (exportedRecordingsInterval) {
        clearInterval(exportedRecordingsInterval);
    }
    
    // Start module recordings auto-refresh (every 2 minutes)
    moduleRecordingsInterval = setInterval(() => {
        // Only refresh if module recordings tab is active
        if (currentActiveTab === 'module-recordings') {
            console.log('Auto-refreshing module recordings...');
            updateModuleRecordings();
        }
    }, 120000);
    console.log('Started module recordings auto-refresh');
}

function startExportedRecordingsAutoRefresh() {
    // Clear any existing intervals
    if (moduleRecordingsInterval) {
        clearInterval(moduleRecordingsInterval);
    }
    if (exportedRecordingsInterval) {
        clearInterval(exportedRecordingsInterval);
    }
    
    // Start exported recordings auto-refresh (every 2 minutes)
    exportedRecordingsInterval = setInterval(() => {
        // Only refresh if exported recordings tab is active
        if (currentActiveTab === 'exported-recordings') {
            console.log('Auto-refreshing exported recordings...');
            loadExportedRecordings();
        }
    }, 120000);
    console.log('Started exported recordings auto-refresh');
}

// Start with module recordings auto-refresh by default (since that tab is active initially)
startModuleRecordingsAutoRefresh();

// Export destination filter functionality
const exportDestinationFilter = document.getElementById('export-destination-filter');
const clearExportFilter = document.getElementById('clear-export-filter');
let currentExportDestinationFilter = 'all';
let allExportedRecordings = []; // Store all exported recordings for filtering

// Event listener for export destination filter
if (exportDestinationFilter) {
    exportDestinationFilter.addEventListener('change', function() {
        currentExportDestinationFilter = this.value;
        filterExportedRecordings();
    });
} else {
    console.error('Export destination filter not found');
}

// Event listener for clear export filter button
if (clearExportFilter) {
    clearExportFilter.addEventListener('click', function() {
        if (exportDestinationFilter) exportDestinationFilter.value = 'all';
        currentExportDestinationFilter = 'all';
        filterExportedRecordings();
    });
} else {
    console.error('Clear export filter button not found');
}

// Filter exported recordings based on destination
function filterExportedRecordings() {
    const exportedRecordingsList = document.querySelector('#exported-recordings .recordings-list');
    if (!exportedRecordingsList || allExportedRecordings.length === 0) return;

    let filteredRecordings = allExportedRecordings;

    if (currentExportDestinationFilter !== 'all') {
        filteredRecordings = allExportedRecordings.filter(recording => {
            // Use the destination field from the recording data
            const destination = recording.destination || 'controller'; // Default to controller if not specified
            return destination === currentExportDestinationFilter;
        });
    }

    // Display filtered recordings
    if (filteredRecordings.length === 0) {
        exportedRecordingsList.innerHTML = '<p>No exported recordings found for selected destination</p>';
    } else {
        // Group filtered recordings by folder structure
        const filteredRecordingsByFolder = groupExportedRecordingsByFolder(filteredRecordings);
        
        // Generate HTML for grouped filtered recordings
        const filteredRecordingsHTML = generateGroupedExportedRecordingsHTML(filteredRecordingsByFolder);
        exportedRecordingsList.innerHTML = filteredRecordingsHTML;
        
        // Add event listeners for filtered recordings
        addExportedRecordingsEventListeners();
    }
}

// Handle module status changes (online/offline)
socket.on('module_status_change', function(data) {
    console.log('Module status change:', data);
    const moduleId = data.module_id;
    const status = data.status;
    
    // Find the module item for this module
    const moduleItem = document.querySelector(`[data-module-id="${moduleId}"]`);
    if (moduleItem) {
        if (status === 'offline') {
            // Mark module as offline
            moduleItem.classList.add('offline');
            
            // Add offline indicator to module name
            const moduleName = moduleItem.querySelector('.module-name');
            if (moduleName && !moduleName.innerHTML.includes('OFFLINE')) {
                moduleName.innerHTML = `${moduleId} <span class="offline-indicator">(OFFLINE)</span>`;
            }
        } else if (status === 'online') {
            // Mark module as online
            moduleItem.classList.remove('offline');
            
            // Remove offline indicator from module name
            const moduleName = moduleItem.querySelector('.module-name');
            if (moduleName) {
                moduleName.innerHTML = moduleId;
            }
        }
    }
});

// Delete all recordings for a specific experiment within a module
function deleteExperimentRecordings(moduleId, experimentName) {
    const recordingCount = document.querySelectorAll(`[data-module-id="${moduleId}"] [data-experiment="${experimentName}"] .recording-item`).length;
    
    if (confirm(`Are you sure you want to delete ALL ${recordingCount} recording${recordingCount !== 1 ? 's' : ''} for experiment "${experimentName}" on module "${moduleId}"?\n\nThis action cannot be undone.`)) {
        // Get all filenames for this experiment on this module
        const filenames = [];
        document.querySelectorAll(`[data-module-id="${moduleId}"] [data-experiment="${experimentName}"] .recording-item`).forEach(item => {
            const filename = item.querySelector('.recording-name').textContent;
            filenames.push(filename);
        });
        
        // Send single command with multiple filenames
        const filenameParam = filenames.join(',');
        socket.emit('command', {
            type: 'clear_recordings',
            module_id: moduleId,
            params: {
                filename: filenameParam
            }
        });
        
        // Refresh recordings list after a short delay
        setTimeout(refreshRecordings, 2000);
    }
}

// Function to display module recordings
function displayModuleRecordings(recordings) {
    const moduleRecordingsList = document.querySelector('#module-recordings .recordings-list');
    if (!moduleRecordingsList) return;
    
    // Only display if we're on the module recordings tab
    if (currentActiveTab !== 'module-recordings') {
        console.log('Module recordings tab not active, skipping display');
        return;
    }
    
    console.log('Displaying module recordings:', recordings);
    
    if (recordings.length === 0) {
        moduleRecordingsList.innerHTML = '<p>No recordings found on any modules</p>';
        return;
    }
    
    // Group recordings by experiment name
    const recordingsByExperiment = groupRecordingsByExperiment(recordings);
    
    // Generate HTML for grouped recordings
    const recordingsHTML = generateGroupedRecordingsHTML(recordingsByExperiment);
    
    // Completely replace the content
    moduleRecordingsList.innerHTML = recordingsHTML;
    
    // Add event listeners for dropdown toggles and action buttons
    addRecordingsEventListeners();
}

// Handle exported recordings list response
socket.on('exported_recordings_list', function(data) {
    console.log('Received exported recordings list:', data);
    
    // Only process if we're on the exported recordings tab
    if (currentActiveTab !== 'exported-recordings') {
        console.log('Exported recordings tab not active, skipping display');
        return;
    }
    
    const exportedRecordingsList = document.querySelector('#exported-recordings .recordings-list');
    if (exportedRecordingsList) {
        if (data.exported_recordings && data.exported_recordings.length > 0) {
            // Store all exported recordings for filtering
            allExportedRecordings = data.exported_recordings;
            
            // Apply current filter
            filterExportedRecordings();
        } else if (data.exported_recordings && data.exported_recordings.length === 0) {
            exportedRecordingsList.innerHTML = '<p>No exported recordings found</p>';
            allExportedRecordings = [];
        }
        // If data.exported_recordings is undefined/null, don't change the display
    }
});

// Module recordings management
let allModuleRecordings = []; // Store all recordings from all modules
let pendingModuleResponses = new Set(); // Track which modules we're waiting for
let moduleRecordingsTimeout = null; // Timeout for aggregation

// Function to update module recordings (aggregates from all modules)
function updateModuleRecordings() {
    console.log('Updating module recordings from all modules...');
    
    // Clear any existing timeout
    if (moduleRecordingsTimeout) {
        clearTimeout(moduleRecordingsTimeout);
        moduleRecordingsTimeout = null;
    }
    
    // Clear the recordings array to prevent duplicates
    allModuleRecordings = [];
    pendingModuleResponses.clear();
    
    // Request list of modules
    socket.emit('get_modules');
}

// Handle modules list response
socket.on('modules_list', function(data) {
    console.log('Received modules list:', data);
    const modules = data.modules || [];
    
    if (modules.length === 0) {
        console.log('No modules found');
        displayModuleRecordings([]);
        return;
    }
    
    // Clear previous recordings and set up pending responses
    allModuleRecordings = [];
    pendingModuleResponses.clear();
    
    // Add all module IDs to pending set
    modules.forEach(module => {
        if (module.id) {
            pendingModuleResponses.add(module.id);
        }
    });
    
    console.log(`Requesting recordings from ${pendingModuleResponses.size} modules:`, Array.from(pendingModuleResponses));
    
    // Request recordings from each module using the correct command format
    modules.forEach(module => {
        if (module.id) {
            socket.emit('command', {
                type: 'list_recordings',
                module_id: module.id,
                params: {}
            });
        }
    });
    
    // Set timeout to display results even if some modules don't respond
    moduleRecordingsTimeout = setTimeout(() => {
        console.log('Module recordings timeout - displaying what we have');
        displayModuleRecordings(allModuleRecordings);
        pendingModuleResponses.clear();
    }, 5000); // 5 second timeout
});

// Handle individual module recordings response
socket.on('module_recordings', function(data) {
    console.log('Received module recordings from', data.module_id, ':', data.recordings);
    
    // Remove this module from pending responses
    pendingModuleResponses.delete(data.module_id);
    
    // Add recordings to our collection (with module_id for reference)
    if (data.recordings && Array.isArray(data.recordings)) {
        data.recordings.forEach(recording => {
            recording.module_id = data.module_id; // Add module_id to each recording
            allModuleRecordings.push(recording);
        });
    }
    
    console.log(`Module ${data.module_id} responded. ${pendingModuleResponses.size} modules still pending.`);
    
    // If all modules have responded, display the results
    if (pendingModuleResponses.size === 0) {
        if (moduleRecordingsTimeout) {
            clearTimeout(moduleRecordingsTimeout);
            moduleRecordingsTimeout = null;
        }
        console.log('All modules responded, displaying aggregated recordings');
        displayModuleRecordings(allModuleRecordings);
    }
});