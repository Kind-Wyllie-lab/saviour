class ModuleItem extends HTMLElement {
    constructor() {
        super();
    }

    // Define the attributes we want to observe
    static get observedAttributes() {
        return ['module-id', 'module-type', 'module-ip'];
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

        this.innerHTML = `
            <div class="module-item">
                <p>ID: ${id}</p>
                <p>Type: ${type}</p>
                <p>IP: ${ip}</p>
            </div>
        `;
    }
}

customElements.define('module-item', ModuleItem);