class Header extends HTMLElement {
    constructor() {
        super();
    }

    connectedCallback() {
        this.innerHTML = `
            <header class="main-header">
                <div class="header-content">
                    <div class="logo-container">
                        <img src="/static/uofe_logo_alpha.png" alt="UoE Logo" class="logo">
                        <img src="/static/sidb_logo_alpha.png" alt="SIDB Logo" class="logo">
                        <h1>Habitat Controller</h1>
                    </div>
                    <nav class="main-nav">
                        <ul>
                            <li><a href="/" class="nav-link">Dashboard</a></li>
                            <li><a href="/recordings" class="nav-link">Recordings</a></li>
                            <li><a href="/guide" class="nav-link">User Guide</a></li>
                        </ul>
                    </nav>
                </div>
            </header>
        `;
    }
}

customElements.define('main-header', Header); 