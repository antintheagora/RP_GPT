/* The palette, and where it comes from.
 *
 * The play screen was built out of Tailwind's stock `slate` -- and `night`
 * was #0f172a, which is slate-900: a *blue* black. So every card in the game
 * sat in cold blue-grey while the rest of the app -- the world cards, the
 * Continue strip, the live chronicle, the frame art -- was warm near-black,
 * rust and cream. Two applications on one screen.
 *
 * Nothing here is invented. The values are the ones already in app.css:
 * #e6dccb and #8a7a5c and #a89a80 from the chronicle, #b3311f from the
 * Continue cards, #fdf5e4 from the world selectors.
 *
 * This lived inline in base.html and was handed to the Tailwind CDN's
 * runtime compiler. It is a build input now, so the game ships a real
 * stylesheet and asks the internet for nothing.
 */
module.exports = {
  content: [
    '../../ui/webapp/templates/**/*.html',
    '../../ui/webapp/static/*.js',
    // Class names built in Python and sent to the browser in a payload never
    // appear in a template, so the scanner cannot see them.
    '../../ui/webapp/*.py',
  ],
  theme: {
    extend: {
      colors: {
        /* grounds, darkest first */
        pitch: '#0b0908',    /* wells: inputs, an unfilled clock segment */
        night: '#14100e',    /* the page itself */
        soot: '#1c1715',     /* a card lifted off it */
        hearth: '#2a221e',   /* a quiet button, a hover */
        edge: '#332a24',     /* hairlines */

        /* ink, brightest first */
        bone: '#fdf5e4',     /* headings */
        parchment: '#e6dccb',/* body */
        tan: '#a89a80',      /* secondary */
        dust: '#8a7a5c',     /* labels and meta */
        ash: '#7f7159',      /* the dimmest thing still meant to be read */

        /* the three things the game needs to say in colour */
        rust: '#b3311f',       /* it is coming for you */
        /* Rust is 2.96:1 against a card. That is fine for a filled clock
           segment or a border and far too dark to read a word in, and it was
           carrying "Closing in", every wound, and the line that tells you the
           campaign is over. So: rust builds, flare speaks. */
        flare: '#d97757',
        brass: '#d8b26a',      /* you are getting somewhere; also temptation */
        verdigris: '#6f8f86',  /* someone is talking */
        ember: '#f97316',      /* the button you actually press */
      },
      borderRadius: {
        /* Tailwind's rounded-xl is 12px, which is a SaaS dashboard. The cards
           this is modelled on are almost square. */
        sm: '3px',
      },
      fontFamily: {
        fantasy: ['Cinzel', 'serif'],
        body: ['"IM Fell English"', 'serif'],
      },
    },
  },
  plugins: [require('@tailwindcss/forms')],
};
