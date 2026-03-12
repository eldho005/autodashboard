/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./*.html"],
  theme: {
    extend: {
      fontFamily: { sans: ['Inter', 'sans-serif'] },
      colors: {
        // Custom shades used across pages
        slate: { 850: '#151e2e' },
        brand: '#4f46e5',
      },
      boxShadow: {
        // meta dashboard.html
        'card':   '0 4px 6px -1px rgba(0, 0, 0, 0.02), 0 2px 4px -1px rgba(0, 0, 0, 0.02)',
        'subtle': '0 1px 2px 0 rgba(0, 0, 0, 0.05)',
        // Auto dashboard.html
        'soft':   '0 4px 20px -2px rgba(0, 0, 0, 0.05)',
        'glow':   '0 0 10px rgba(99, 102, 241, 0.2)',
      },
      animation: {
        'flash-green': 'flashGreen 0.5s ease-in-out',
        'fade-in':     'fadeIn 0.3s ease-out',
      },
      keyframes: {
        flashGreen: {
          '0%, 100%': { backgroundColor: 'transparent' },
          '50%':      { backgroundColor: '#dcfce7' },
        },
        fadeIn: {
          '0%':   { opacity: '0', transform: 'translateY(5px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  // Classes added dynamically via JavaScript classList.add() — scanner can't detect these
  safelist: [
    'bg-emerald-600',
    'hover:bg-emerald-700',
    'bg-emerald-50',
    'border-emerald-200',
  ],
  plugins: [],
}
