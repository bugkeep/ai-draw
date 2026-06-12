// frontend/js/canvas.js
class CanvasManager {
    constructor(canvasId) {
        this.canvas = new fabric.Canvas(canvasId, {
            backgroundColor: '#ffffff',
            selection: true
        });
        this.history = [];
        this.historyIndex = -1;
        this.maxHistory = 50;
        
        this.canvas.on('object:modified', () => this.saveState());
        this.saveState();
    }
    
    saveState() {
        const json = JSON.stringify(this.canvas.toJSON());
        
        if (this.historyIndex < this.history.length - 1) {
            this.history = this.history.slice(0, this.historyIndex + 1);
        }
        
        this.history.push(json);
        
        if (this.history.length > this.maxHistory) {
            this.history.shift();
        } else {
            this.historyIndex++;
        }
    }
    
    undo() {
        if (this.historyIndex > 0) {
            this.historyIndex--;
            this.canvas.loadFromJSON(this.history[this.historyIndex], () => {
                this.canvas.renderAll();
            });
        }
    }
    
    redo() {
        if (this.historyIndex < this.history.length - 1) {
            this.historyIndex++;
            this.canvas.loadFromJSON(this.history[this.historyIndex], () => {
                this.canvas.renderAll();
            });
        }
    }
    
    clear() {
        this.canvas.clear();
        this.canvas.backgroundColor = '#ffffff';
        this.canvas.renderAll();
        this.saveState();
    }
    
    executeCode(code) {
        try {
            const fn = new Function('canvas', code);
            fn(this.canvas);
            this.canvas.renderAll();
            this.saveState();
            return true;
        } catch (e) {
            console.error('Execute code error:', e);
            return false;
        }
    }
    
    getState() {
        const objects = this.canvas.getObjects().map(obj => ({
            type: obj.type,
            left: Math.round(obj.left),
            top: Math.round(obj.top),
            width: obj.width ? Math.round(obj.width * obj.scaleX) : undefined,
            height: obj.height ? Math.round(obj.height * obj.scaleY) : undefined,
            radius: obj.radius ? Math.round(obj.radius * obj.scaleX) : undefined,
            fill: obj.fill,
            stroke: obj.stroke,
            text: obj.text
        }));
        
        return {
            objects,
            canvas_size: {
                width: this.canvas.getWidth(),
                height: this.canvas.getHeight()
            }
        };
    }
    
    resize(width, height) {
        this.canvas.setDimensions({ width, height });
        this.canvas.renderAll();
    }
}

window.CanvasManager = CanvasManager;