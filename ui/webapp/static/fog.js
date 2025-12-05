/**
 * Dynamic Fog Effect
 * 
 * A volumetric fog system using HTML5 Canvas.
 * Configurable via the FOG_CONFIG object.
 */

const FOG_CONFIG = {
    // Appearance
    particleCount: 80,        // Number of fog particles
    baseSize: 400,            // Base size of a particle in pixels
    sizeVariation: 300,       // Random size variation (increased for blobby look)
    color: '200, 220, 255',   // RGB values for the fog (bright/misty)
    baseOpacity: 0.2,         // Base opacity of particles
    brightness: 1.5,          // Global brightness multiplier

    // Movement
    speed: 0.5,               // Reduced base speed for wispiness
    drift: 1.5,               // Higher random drift for chaotic movement
    windX: 0.05,              // Very slight horizontal wind
    windY: -0.05,             // Very slight vertical wind

    // Lifecycle (frames)
    minLife: 400,             // Minimum lifespan
    maxLife: 1200,            // Maximum lifespan

    // Interaction
    interactionRadius: 400,   // Radius of mouse influence
    interactionForce: 0.2,    // Strength of mouse push/pull

    // System
    fpsLimit: 60,
    zIndexBackground: -15,    // Behind UI
    zIndexForeground: 50,     // In front of UI
    foregroundRatio: 0.2      // Percentage of particles in foreground
};


class FogParticle {
    constructor(w, h, layer) {
        this.w = w;
        this.h = h;
        this.layer = layer; // 'fg' or 'bg'
        this.reset(true);
    }

    reset(randomizePos = false) {
        // Position
        this.x = randomizePos ? Math.random() * this.w : Math.random() * this.w;
        this.y = randomizePos ? Math.random() * this.h : Math.random() * this.h;

        // Velocity - more random, less directional
        this.vx = (Math.random() - 0.5) * FOG_CONFIG.drift + FOG_CONFIG.windX;
        this.vy = (Math.random() - 0.5) * FOG_CONFIG.drift + FOG_CONFIG.windY;

        // Appearance
        this.size = FOG_CONFIG.baseSize + (Math.random() * FOG_CONFIG.sizeVariation);
        this.baseAlpha = (Math.random() * 0.5 + 0.5) * FOG_CONFIG.baseOpacity;
        this.rotation = Math.random() * Math.PI * 2;
        this.rotSpeed = (Math.random() - 0.5) * 0.002;

        // Lifecycle
        this.age = 0;
        this.lifespan = FOG_CONFIG.minLife + Math.random() * (FOG_CONFIG.maxLife - FOG_CONFIG.minLife);
        // Start at random age if randomizing pos, to avoid everything fading in at once
        if (randomizePos) {
            this.age = Math.random() * this.lifespan;
        }
    }

    update(mouseX, mouseY) {
        // Aging
        this.age++;
        if (this.age >= this.lifespan) {
            this.reset();
            this.age = 0; // Reset starts at 0 for new particles
        }

        // Calculate fade in/out using sine wave
        // 0 -> 1 -> 0 over the lifespan
        const lifeRatio = this.age / this.lifespan;
        this.currentAlpha = this.baseAlpha * Math.sin(lifeRatio * Math.PI);

        // Base movement
        this.x += this.vx * FOG_CONFIG.speed;
        this.y += this.vy * FOG_CONFIG.speed;
        this.rotation += this.rotSpeed;

        // Add a slight "wobble" for wispiness
        this.x += Math.sin(this.age * 0.01) * 0.2;

        // Mouse interaction
        if (mouseX !== null && mouseY !== null) {
            const dx = this.x - mouseX;
            const dy = this.y - mouseY;
            const dist = Math.sqrt(dx * dx + dy * dy);

            if (dist < FOG_CONFIG.interactionRadius) {
                const force = (1 - dist / FOG_CONFIG.interactionRadius) * FOG_CONFIG.interactionForce * 10;
                const angle = Math.atan2(dy, dx);
                this.x += Math.cos(angle) * force;
                this.y += Math.sin(angle) * force;
            }
        }

        // Wrap around (still useful even with fading, just in case)
        const buffer = this.size / 2;
        if (this.x < -buffer) this.x = this.w + buffer;
        if (this.x > this.w + buffer) this.x = -buffer;
        if (this.y < -buffer) this.y = this.h + buffer;
        if (this.y > this.h + buffer) this.y = -buffer;
    }

    draw(ctx) {
        ctx.save();
        ctx.translate(this.x, this.y);
        ctx.rotate(this.rotation);
        ctx.globalAlpha = this.currentAlpha * FOG_CONFIG.brightness;

        // Draw a soft procedural "blob"
        // We use a radial gradient to create a soft puff
        const grad = ctx.createRadialGradient(0, 0, 0, 0, 0, this.size / 2);

        // Inner color (opaque relative to globalAlpha)
        grad.addColorStop(0, `rgba(${FOG_CONFIG.color}, 1)`);
        // Mid color for extra softness
        grad.addColorStop(0.6, `rgba(${FOG_CONFIG.color}, 0.5)`);
        // Outer fade
        grad.addColorStop(1, `rgba(${FOG_CONFIG.color}, 0)`);

        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(0, 0, this.size / 2, 0, Math.PI * 2);
        ctx.fill();

        ctx.restore();
    }
}

class FogSystem {
    constructor() {
        // Background Canvas
        this.canvasBg = this.createCanvas('fog-canvas-bg', FOG_CONFIG.zIndexBackground);
        this.ctxBg = this.canvasBg.getContext('2d');

        // Foreground Canvas
        this.canvasFg = this.createCanvas('fog-canvas-fg', FOG_CONFIG.zIndexForeground);
        this.ctxFg = this.canvasFg.getContext('2d');

        this.particles = [];
        this.mouseX = null;
        this.mouseY = null;

        this.resize();
        this.resizeHandler = () => this.resize();
        window.addEventListener('resize', this.resizeHandler);
        window.addEventListener('mousemove', (e) => {
            this.mouseX = e.clientX;
            this.mouseY = e.clientY;
        });

        this.initParticles();
        this.destroyed = false;
        this.animate();
    }

    createCanvas(id, zIndex) {
        const canvas = document.createElement('canvas');
        canvas.id = id;
        canvas.style.position = 'fixed';
        canvas.style.top = '0';
        canvas.style.left = '0';
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        canvas.style.pointerEvents = 'none';
        canvas.style.zIndex = zIndex;
        canvas.style.mixBlendMode = 'screen';
        document.body.appendChild(canvas);
        return canvas;
    }

    resize() {
        this.width = window.innerWidth;
        this.height = window.innerHeight;

        this.canvasBg.width = this.width;
        this.canvasBg.height = this.height;

        this.canvasFg.width = this.width;
        this.canvasFg.height = this.height;
    }

    initParticles() {
        this.particles = [];
        for (let i = 0; i < FOG_CONFIG.particleCount; i++) {
            const layer = Math.random() < FOG_CONFIG.foregroundRatio ? 'fg' : 'bg';
            this.particles.push(new FogParticle(this.width, this.height, layer));
        }
    }

    animate() {
        // Stop if destroyed
        if (this.destroyed) {
            return;
        }

        this.ctxBg.clearRect(0, 0, this.width, this.height);
        this.ctxFg.clearRect(0, 0, this.width, this.height);

        this.particles.forEach(p => {
            p.update(this.mouseX, this.mouseY);
            if (p.layer === 'bg') {
                p.draw(this.ctxBg);
            } else {
                p.draw(this.ctxFg);
            }
        });

        this.animationFrameId = requestAnimationFrame(() => this.animate());
    }

    detach() {
        if (this.canvasBg) this.canvasBg.remove();
        if (this.canvasFg) this.canvasFg.remove();
    }

    attach() {
        if (this.canvasBg) document.body.appendChild(this.canvasBg);
        if (this.canvasFg) document.body.appendChild(this.canvasFg);
    }

    destroy() {
        this.destroyed = true;
        if (this.animationFrameId) {
            cancelAnimationFrame(this.animationFrameId);
        }
        // Remove event listeners
        window.removeEventListener('resize', this.resizeHandler);
        // Canvases are removed by HTMX usually, but we can ensure they are gone
        if (this.canvasBg.parentNode) this.canvasBg.remove();
        if (this.canvasFg.parentNode) this.canvasFg.remove();
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.fogSystem = new FogSystem();
});

// Handle HTMX swaps to persist fog
document.body.addEventListener('htmx:beforeSwap', (event) => {
    // Only detach if the swap is targeting the body (which would destroy the canvas)
    if (event.target === document.body && window.fogSystem) {
        window.fogSystem.detach();
    }
});

document.body.addEventListener('htmx:afterSwap', (event) => {
    // Only re-attach if we detached (or if it's missing)
    if (event.target === document.body) {
        if (window.fogSystem) {
            window.fogSystem.attach();
        } else {
            window.fogSystem = new FogSystem();
        }
    }
});
