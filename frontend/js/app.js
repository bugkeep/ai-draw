// frontend/js/app.js
class App {
    constructor() {
        this.canvas = new CanvasManager('fabric-canvas');
        this.speech = new SpeechManager();
        this.isProcessing = false;
        
        this.initElements();
        this.initEventListeners();
        this.initSpeechCallbacks();
    }
    
    initElements() {
        this.btnRecord = document.getElementById('btn-record');
        this.btnText = this.btnRecord.querySelector('.btn-text');
        this.transcript = document.getElementById('transcript');
        this.chatLog = document.getElementById('chat-log');
        this.status = document.getElementById('status');
        this.btnUndo = document.getElementById('btn-undo');
        this.btnRedo = document.getElementById('btn-redo');
        this.btnClear = document.getElementById('btn-clear');
        this.apiKeyInput = document.getElementById('api-key');
        this.providerSelect = document.getElementById('provider');
    }
    
    initEventListeners() {
        this.btnRecord.addEventListener('click', () => this.toggleRecording());
        this.btnUndo.addEventListener('click', () => this.canvas.undo());
        this.btnRedo.addEventListener('click', () => this.canvas.redo());
        this.btnClear.addEventListener('click', () => this.canvas.clear());
    }
    
    initSpeechCallbacks() {
        this.speech.onResult = (text, isFinal) => {
            this.transcript.textContent = text;
            
            if (isFinal && text.trim()) {
                this.sendMessage(text.trim());
            }
        };
        
        this.speech.onStatusChange = (status, error) => {
            switch (status) {
                case 'recording':
                    this.btnRecord.classList.add('recording');
                    this.btnText.textContent = 'Stop Recording';
                    this.setStatus('Listening...');
                    break;
                case 'idle':
                    this.btnRecord.classList.remove('recording');
                    this.btnText.textContent = 'Start Recording';
                    this.setStatus('Ready');
                    break;
                case 'error':
                    this.btnRecord.classList.remove('recording');
                    this.btnText.textContent = 'Start Recording';
                    this.setStatus('Error: ' + error);
                    break;
            }
        };
    }
    
    toggleRecording() {
        if (this.speech.isRecording) {
            this.speech.stop();
        } else {
            this.speech.start();
        }
    }
    
    setStatus(text) {
        this.status.textContent = text;
    }
    
    addChatMessage(text, type = 'user') {
        const div = document.createElement('div');
        div.className = `chat-message ${type}`;
        div.innerHTML = `<p>${this.escapeHtml(text)}</p>`;
        this.chatLog.appendChild(div);
        this.chatLog.scrollTop = this.chatLog.scrollHeight;
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    async sendMessage(message) {
        if (this.isProcessing) return;
        
        this.isProcessing = true;
        this.setStatus('Processing...');
        this.addChatMessage(message, 'user');
        
        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message,
                    canvas_state: this.canvas.getState()
                })
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            
            if (data.code) {
                this.canvas.executeCode(data.code);
            }
            
            if (data.description) {
                this.addChatMessage(data.description, 'assistant');
                await this.speech.speak(data.description);
            }
            
        } catch (error) {
            console.error('Send message error:', error);
            this.addChatMessage('Error: ' + error.message, 'system');
            this.setStatus('Error');
        } finally {
            this.isProcessing = false;
            this.setStatus('Ready');
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.app = new App();
});
