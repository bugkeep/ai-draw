// frontend/js/speech.js
class SpeechManager {
    constructor() {
        this.recognition = null;
        this.synthesis = window.speechSynthesis;
        this.isRecording = false;
        this.onResult = null;
        this.onStatusChange = null;
        
        this.initRecognition();
    }
    
    initRecognition() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        
        if (!SpeechRecognition) {
            console.warn('Speech Recognition not supported');
            return;
        }
        
        this.recognition = new SpeechRecognition();
        this.recognition.continuous = false;
        this.recognition.interimResults = true;
        this.recognition.lang = 'zh-CN';
        
        this.recognition.onresult = (event) => {
            const transcript = Array.from(event.results)
                .map(result => result[0].transcript)
                .join('');
            
            if (this.onResult) {
                this.onResult(transcript, event.results[0].isFinal);
            }
        };
        
        this.recognition.onend = () => {
            this.isRecording = false;
            if (this.onStatusChange) {
                this.onStatusChange('idle');
            }
        };
        
        this.recognition.onerror = (event) => {
            console.error('Speech recognition error:', event.error);
            this.isRecording = false;
            if (this.onStatusChange) {
                this.onStatusChange('error', event.error);
            }
        };
    }
    
    start() {
        if (!this.recognition) {
            console.error('Speech Recognition not available');
            return false;
        }
        
        if (this.isRecording) {
            return false;
        }
        
        this.isRecording = true;
        if (this.onStatusChange) {
            this.onStatusChange('recording');
        }
        
        this.recognition.start();
        return true;
    }
    
    stop() {
        if (this.recognition && this.isRecording) {
            this.recognition.stop();
        }
    }
    
    speak(text) {
        return new Promise((resolve) => {
            if (!this.synthesis) {
                resolve();
                return;
            }
            
            this.synthesis.cancel();
            
            const utterance = new SpeechSynthesisUtterance(text);
            utterance.lang = 'zh-CN';
            utterance.rate = 1.0;
            utterance.onend = resolve;
            utterance.onerror = resolve;
            
            this.synthesis.speak(utterance);
        });
    }
    
    speakAsync(text) {
        return this.speak(text);
    }
}

window.SpeechManager = SpeechManager;
