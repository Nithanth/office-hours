# Font Awesome Assets

This directory stores the self-hosted subset of Font Awesome used by Lux Scholar.

How to populate:

1. Install via npm (recommended):

```bash
npm i @fortawesome/fontawesome-free --save-dev
```

1. Copy the required files:

```bash
VENDOR=static/vendor/fontawesome
mkdir -p $VENDOR/css $VENDOR/webfonts
cp node_modules/@fortawesome/fontawesome-free/css/all.min.css $VENDOR/css/
cp node_modules/@fortawesome/fontawesome-free/webfonts/fa-solid-900.woff2 $VENDOR/webfonts/
cp node_modules/@fortawesome/fontawesome-free/webfonts/fa-solid-900.woff $VENDOR/webfonts/
# add any additional style variants you use (regular, brands, etc.)
```

1. Commit the copied files so the app can serve them from `/static/vendor/fontawesome/…`.

After this, the template includes

```html
<link rel="stylesheet" href="/static/vendor/fontawesome/css/all.min.css">
```

so icons work even when CDNs are blocked.
