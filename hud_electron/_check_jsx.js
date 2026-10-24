const Babel = require('@babel/standalone');
const fs = require('fs');
const path = require('path');

// Auto-discover all JSX files using the exact presets+plugins the browser uses.
const browserOpts = {
  presets: ['react', 'env'],
  plugins: ['transform-class-properties', 'transform-object-rest-spread', 'transform-flow-strip-types'],
};

const files = fs.readdirSync(__dirname).filter(f => f.endsWith('.jsx')).sort();
let errors = 0;
for (const f of files) {
  try {
    const src = fs.readFileSync(path.join(__dirname, f), 'utf8');
    Babel.transform(src, { ...browserOpts, filename: f, sourceFileName: f });
    console.log('OK  ' + f);
  } catch (e) {
    console.log('ERR ' + f + ' :: ' + e.message);
    errors++;
  }
}
if (errors) {
  console.error('\n' + errors + ' file(s) failed — fix before running the app.');
  process.exit(1);
} else {
  console.log('\nAll ' + files.length + ' JSX files OK.');
}
