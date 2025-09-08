class ModuleItem extends HTMLElement {
    constructor() {
        super();
    }

    // Define the attributes we want to observe
    static get observedAttributes() {
        return ['module-id', 'module-type', 'module-ip', 'recording'];
    }

    // Called when the element is added to the DOM
    connectedCallback() {
        this.render();
    }

    // Called when observed attributes change
    attributeChangedCallback(name, oldValue, newValue) {
        if (oldValue !== newValue) {
            this.render();
        }
    }

    render() {
        const id = this.getAttribute('module-id') || '';
        const type = this.getAttribute('module-type') || '';
        const ip = this.getAttribute('module-ip') || '';
        const recording = this.getAttribute('recording') === 'true';

        this.innerHTML = `
            <div class="module-item ${recording ? 'recording' : ''}">
                <div class="module-header">
                    <p>ID: ${id}</p>
                    ${recording ? '<span class="recording-indicator" title="Recording">🔴</span>' : ''}
                </div>
                <p>Type: ${type}</p>
                <p>IP: ${ip}</p>
            </div>
        `;
    }
}

customElements.define('module-item', ModuleItem);