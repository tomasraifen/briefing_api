import re
import httpx
from fastapi import APIRouter, Query

router = APIRouter()


_TECH_SIGNALS = [
    (r'shopify', 'Shopify'),
    (r'woocommerce', 'WooCommerce'),
    (r'wp-content|wordpress', 'WordPress'),
    (r'magento', 'Magento'),
    (r'vtex', 'VTEX'),
    (r'prestashop', 'PrestaShop'),
    (r'hubspot', 'HubSpot'),
    (r'salesforce', 'Salesforce'),
    (r'zoho', 'Zoho'),
    (r'pipedrive', 'Pipedrive'),
    (r'google-analytics|gtag\(', 'Google Analytics'),
    (r'hotjar', 'Hotjar'),
    (r'intercom', 'Intercom'),
    (r'zendesk', 'Zendesk'),
    (r'sap\.', 'SAP'),
    (r'oracle', 'Oracle'),
    (r'siigo', 'Siigo'),
    (r'aspel', 'Aspel'),
    (r'react', 'React'),
    (r'next\.js|nextjs', 'Next.js'),
    (r'vue\.js|vuejs', 'Vue.js'),
    (r'angular', 'Angular'),
    (r'jquery', 'jQuery'),
    (r'bootstrap', 'Bootstrap'),
    (r'cloudflare', 'Cloudflare'),
    (r'aws\.amazon|cloudfront', 'AWS'),
    (r'wix\.com|wixsite', 'Wix'),
    (r'squarespace', 'Squarespace'),
    (r'webflow', 'Webflow'),
    (r'stripe', 'Stripe'),
    (r'paypal', 'PayPal'),
    (r'mercadopago', 'MercadoPago'),
    (r'payu', 'PayU'),
    (r'recaptcha', 'Google reCAPTCHA'),
]
_TECH_SIGNALS = [(re.compile(p, re.I), name) for p, name in _TECH_SIGNALS]


def _detect_tech_stack(domain: str) -> str:
    """Detecta tech stack haciendo scraping básico del dominio."""
    detected = []
    for scheme in ('https', 'http'):
        try:
            r = httpx.get(f"{scheme}://{domain}", timeout=8.0, follow_redirects=True,
                          headers={'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)'})
            if r.status_code == 200:
                html = r.text
                for pattern, name in _TECH_SIGNALS:
                    if pattern.search(html) and name not in detected:
                        detected.append(name)
                break
        except Exception:
            continue
    return ', '.join(detected) if detected else None


@router.get("/enrich/tech-stack")
def get_tech_stack(domain: str = Query(..., description="Dominio a analizar, ej: empresa.com")):
    """
    Detecta tech stack de un dominio vía scraping básico (regex de firmas conocidas en el HTML).
    Usado por la ingesta de Apollo para enriquecer leads recién descargados.
    """
    domain = domain.strip().lower().replace("https://", "").replace("http://", "").split("/")[0]

    tech_stack = _detect_tech_stack(domain)

    stack_cat = None
    if tech_stack:
        ts_lower = tech_stack.lower()
        if any(x in ts_lower for x in ['shopify', 'woocommerce', 'vtex', 'magento', 'prestashop']):
            stack_cat = 'ecommerce'
        elif any(x in ts_lower for x in ['sap', 'oracle', 'siigo', 'aspel', 'erp']):
            stack_cat = 'erp'
        elif any(x in ts_lower for x in ['google analytics', 'hotjar', 'metabase', 'tableau', 'power bi']):
            stack_cat = 'analytics'
        else:
            stack_cat = 'basico'

    return {
        "domain": domain,
        "tech_stack": tech_stack,
        "stack_categoria": stack_cat,
        "source": "scraping",
    }
