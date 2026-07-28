// JavaScript for BeyondScreen Homepage

document.addEventListener('DOMContentLoaded', function() {
    // Navbar scroll effect
    const navbar = document.getElementById('navbar');
    const navLinks = document.getElementById('navLinks');

    window.addEventListener('scroll', function() {
        if (window.scrollY > 50) {
            navbar.classList.add('scrolled');
        } else {
            navbar.classList.remove('scrolled');
        }
    });

    // Mobile menu toggle (if needed in future)
    // For now, we'll keep it simple as the desktop nav is sufficient

    // Intersection Observer for fade-in animations
    const observerOptions = {
        threshold: 0.1,
        rootMargin: '0px 0px -50px 0px'
    };

    const observer = new IntersectionObserver(function(entries) {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('animate-fade-in');
                observer.unobserve(target); // Stop observing once animated
            }
        });
    }, observerOptions);

    // Observe all elements with animation classes
    const animatedElements = document.querySelectorAll('.animate-fade-in-up, .animate-fade-in');
    animatedElements.forEach((el, index) => {
        // Add delay based on position if not already set
        if (!el.style.animationDelay) {
            el.style.animationDelay = (index * 0.1) + 's';
        }
        observer.observe(el);
    });

    // Add interactive particles to the hero visual (optional enhancement)
    const visualContainer = document.querySelector('.visual-container');
    if (visualContainer) {
        createParticles(visualContainer);
    }

    // Smooth scrolling for anchor links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function(e) {
            const href = this.getAttribute('href');
            if (href !== "#") {
                e.preventDefault();
                const target = document.querySelector(href);
                if (target) {
                    target.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                }
            }
        });
    });
});

// Function to create floating particles in the hero visual
function createParticles(container) {
    const particleCount = 20;
    const colors = ['var(--accent-gold)', 'var(--accent-teal)', 'rgba(255,255,255,0.1)'];

    for (let i = 0; i < particleCount; i++) {
        const particle = document.createElement('div');
        particle.className = 'visual-particle';

        const size = Math.random() * 3 + 1; // 1-4px
        const x = Math.random() * 100; // 0-100%
        const y = Math.random() * 100; // 0-100%
        const duration = Math.random() * 10 + 5; // 5-15s
        const delay = Math.random() * 5; // 0-5s delay

        particle.style.width = `${size}px`;
        particle.style.height = `${size}px`;
        particle.style.backgroundColor = colors[Math.floor(Math.random() * colors.length)];
        particle.style.left = `${x}%`;
        particle.style.bottom = `${y}%`;
        particle.style.animation = `float ${duration}s ease-in-out ${delay}s infinite`;

        container.appendChild(particle);
    }
}

// Add particle CSS dynamically
const particleStyle = document.createElement('style');
particleStyle.textContent = `
    .visual-particle {
        position: absolute;
        border-radius: 50%;
        pointer-events: none;
        z-index: 1;
    }

    @keyframes float {
        0%, 100% {
            transform: translateY(0) rotate(0deg);
            opacity: 0.3;
        }
        50% {
            transform: translateY(-20px) rotate(180deg);
            opacity: 0.7;
        }
    }

    .visual-container {
        position: relative;
        overflow: hidden;
    }

    .visual-glow {
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        width: 260px;
        height: 260px;
        background: radial-gradient(circle at center, rgba(231, 180, 106, 0.15) 0%, transparent 70%);
        border-radius: 50%;
        animation: pulse 4s ease-in-out infinite;
        z-index: 0;
    }

    .visual-circle {
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        width: 220px;
        height: 220px;
        background: radial-gradient(circle at 30% 30%, rgba(231, 180, 106, 0.2) 0%, transparent 50%),
                    radial-gradient(circle at 70% 70%, rgba(99, 243, 232, 0.2) 0%, transparent 50%);
        border-radius: 50%;
        border: 1px solid rgba(255,255,255,0.08);
        z-index: 1;
    }

    .visual-ring {
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        width: 180px;
        height: 180px;
        border: 1px dashed rgba(255,255,255,0.05);
        border-radius: 50%;
        animation: rotate 20s linear infinite;
        z-index: 1;
    }

    .visual-dot {
        position: absolute;
        top: 50%;
        left: 50%;
        width: 6px;
        height: 6px;
        background: var(--accent-gold);
        border-radius: 50%;
        transform: translate(-50%, -50%);
        z-index: 2;
    }

    .visual-dot:nth-child(1) {
        transform: translate(-50%, -50%) translate(0, -80px);
        animation: orbit 6s ease-in-out infinite;
    }

    .visual-dot:nth-child(2) {
        transform: translate(-50%, -50%) translate(80px, 0);
        animation: orbit 8s ease-in-out 1s infinite reverse;
    }

    .visual-dot:nth-child(3) {
        transform: translate(-50%, -50%) translate(0, 80px);
        animation: orbit 10s ease-in-out 2s infinite;
    }

    .visual-dot:nth-child(4) {
        transform: translate(-50%, -50%) translate(-80px, 0);
        animation: orbit 12s ease-in-out 3s infinite reverse;
    }

    .visual-dot:nth-child(5) {
        transform: translate(-50%, -50%) translate(40px, -40px);
        animation: orbit 14s ease-in-out 4s infinite;
    }

    .visual-line {
        position: absolute;
        top: 50%;
        left: 50%;
        width: 2px;
        height: 60%;
        background: linear-gradient(to bottom, transparent, rgba(255,255,255,0.2), transparent);
        transform: translate(-50%, -50%) rotate(45deg);
        animation: lineMove 8s linear infinite;
        z-index: 1;
    }

    @keyframes orbit {
        0% { transform: translate(-50%, -50%) rotate(0deg) translateX(0) rotate(0deg); }
        100% { transform: translate(-50%, -50%) rotate(360deg) translateX(0) rotate(-360deg); }
    }

    @keyframes pulse {
        0%, 100% { transform: translate(-50%, -50%) scale(1); opacity: 0.3; }
        50% { transform: translate(-50%, -50%) scale(1.1); opacity: 0.6; }
    }

    @keyframes rotate {
        0% { transform: translate(-50%, -50%) rotate(0deg); }
        100% { transform: translate(-50%, -50%) rotate(360deg); }
    }

    @keyframes lineMove {
        0% { transform: translate(-50%, -50%) rotate(45deg) translateY(0); }
        100% { transform: translate(-50%, -50%) rotate(45deg) translateY(100%); }
    }
`;

document.head.appendChild(particleStyle);