# Proxy Cloudflare pour l'assistant

Ce Worker existe pour une seule raison : **le site est public**. Son code
JavaScript est lisible par n'importe quel visiteur qui ouvre l'inspecteur de
son navigateur. Une clé d'API OpenAI placée dans ce code serait récupérée en
quelques secondes et utilisée aux frais du propriétaire.

La clé vit donc ici, dans un secret Cloudflare que le navigateur ne voit
jamais. Le site envoie une question au Worker, le Worker interroge OpenAI, et
renvoie la réponse.

Le plan gratuit de Cloudflare suffit largement : 100 000 requêtes par jour.

---

## Étape 1 — Créer un compte Cloudflare

1. Aller sur <https://dash.cloudflare.com/sign-up>.
2. Créer un compte avec une adresse e-mail, puis valider le lien reçu.
3. Aucune carte bancaire n'est demandée pour le plan gratuit.

Il n'est **pas** nécessaire d'avoir un nom de domaine : les Workers sont
servis sur une adresse `*.workers.dev` fournie par Cloudflare.

## Étape 2 — Installer wrangler

`wrangler` est l'outil en ligne de commande de Cloudflare. Il demande
Node.js, déjà nécessaire par ailleurs.

```bash
npm install -g wrangler
wrangler --version
```

Puis connecter l'outil au compte. La commande ouvre une page dans le
navigateur pour autoriser l'accès :

```bash
wrangler login
```

## Étape 3 — Créer l'espace de limitation de débit

Sans cet espace, le Worker fonctionne mais **sans garde-fou** : un visiteur
pourrait poser des milliers de questions et épuiser le crédit OpenAI.

```bash
cd worker
wrangler kv namespace create LIMITEUR
```

La commande affiche un identifiant, par exemple :

```
{ binding = "LIMITEUR", id = "a1b2c3d4e5f6..." }
```

Recopier cet identifiant dans `wrangler.toml`, à la place de
`REMPLACER_PAR_L_IDENTIFIANT_KV`.

## Étape 4 — Ajouter la clé OpenAI en secret

```bash
wrangler secret put OPENAI_API_KEY
```

La commande demande la clé et l'enregistre chiffrée côté Cloudflare. Elle
n'apparaît ni dans le dépôt, ni dans `wrangler.toml`, ni dans les journaux.

La clé se crée sur <https://platform.openai.com/api-keys>. Prévoir une
limite de dépense mensuelle dans les réglages de facturation OpenAI : c'est
la seconde barrière, après la limitation de débit du Worker.

## Étape 5 — Déployer

```bash
wrangler deploy
```

La commande affiche l'adresse du Worker :

```
https://assistant-suivi-marches.<votre-sous-domaine>.workers.dev
```

## Étape 6 — Brancher le site

Recopier cette adresse dans `site/js/config.js` :

```js
urlAssistant: "https://assistant-suivi-marches.<votre-sous-domaine>.workers.dev",
```

Committer et pousser : GitHub Pages publiera la nouvelle version. Tant que ce
champ reste vide, l'assistant affiche un message expliquant qu'il n'est pas
configuré — il ne casse pas la page.

## Étape 7 — Restreindre l'origine

Une fois l'adresse GitHub Pages connue, remplacer dans `wrangler.toml` :

```toml
ORIGINE_AUTORISEE = "https://<utilisateur>.github.io"
```

puis `wrangler deploy` à nouveau. Le Worker n'acceptera plus que les
requêtes venues du site, ce qui empêche un tiers d'utiliser le proxy depuis
sa propre page.

---

## Vérifier que ça marche

```bash
curl -X POST https://assistant-suivi-marches.<sous-domaine>.workers.dev \
  -H "Content-Type: application/json" \
  -d '{"question":"Que mesure le MVRV ?","contexte":{"mvrv":1.49}}'
```

La réponse attendue ressemble à :

```json
{"reponse":"Le MVRV rapporte …","limitation_active":true,"questions_restantes":5}
```

`limitation_active: false` signale que l'espace KV n'est pas branché : le
Worker répond, mais sans limite de débit. Reprendre l'étape 3.

## Réglages

Dans `src/index.js` :

| Constante | Défaut | Rôle |
|---|---|---|
| `FENETRE_SECONDES` | 60 | Durée de la fenêtre de limitation |
| `MAX_PAR_FENETRE` | 6 | Questions autorisées par adresse et par fenêtre |
| `TAILLE_MAX` | 200 000 | Taille maximale du contexte reçu, en octets |

Dans `wrangler.toml`, `MODELE` choisit le modèle OpenAI.

## Ce que le Worker impose, et pourquoi côté serveur

La consigne système — n'utiliser que les chiffres fournis, ne jamais
recommander d'acheter ou de vendre — est posée **dans le Worker**, pas dans
le navigateur. Un visiteur qui modifierait le site sur sa machine ne peut
donc pas la contourner : elle est appliquée là où il n'a pas la main.

Le détail des erreurs de l'API amont n'est jamais relayé au navigateur : ces
messages peuvent contenir des informations de compte ou de facturation qui
n'ont rien à faire dans une page publique.
