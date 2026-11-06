#!/usr/bin/env python3
# Passerelle Orvane <-> Cataclysm: Dark Days Ahead (version 0.I).
#
# Licence : PolyForm Shield 1.0.0, https://polyformproject.org/licenses/shield/1.0.0
# Required Notice: Copyright StoneValley (https://github.com/stonevalleyoff-ai/orvane)
"""Branche Cataclysm: Dark Days Ahead sur le monde d'Orvane.

  preparer  avant de jouer : lit le monde d'Orvane et écrit le mod « orvane »,
            qui ajoute les lignées d'Orvane à la faune sauvage du jeu.
  compter   après avoir joué : lit dans les sauvegardes les bêtes d'Orvane tuées
            depuis le dernier compte et en fait un lot de pressions.
  envoyer   dépose les lots en attente dans Orvane, avec la clé du jeu.
  apres     compter, puis envoyer.
  etat      dit où en est la passerelle.

Python 3.8 ou plus, sans dépendance. Pour lire une sauvegarde compressée (le réglage
par défaut des nouveaux mondes en 0.I), il faut aussi un décompresseur zstd : Python 3.14,
le paquet « zstandard » (pip install zstandard), la bibliothèque libzstd ou la commande zstd.
Le mode d'emploi est dans LISEZ-MOI.md, à côté de ce fichier.
"""

import argparse
import ctypes
import ctypes.util
import datetime
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid

VERSION = '0.1'

# --- Orvane -----------------------------------------------------------------------------

MONDE_URL = 'https://stonevalleyoff-ai.github.io/orvane/mondes/orvane/'
SUPABASE = 'https://rospodujiyltmxitykwc.supabase.co'
CLE_PROJET = 'sb_publishable_F98uYIaPrLTjhj3n7QiBOg_SOtFynn8'
# La clé du jeu est publique : elle signe les dépôts. La clé d'administration ne va jamais ici.
CLE_JEU = 'orv_fd89352bdef3b79c7c2570fe52d5f1dc'
JEU = 'cataclysm-dark-days-ahead'

MAX_EVENEMENTS = 500
MAX_OCTETS = 64 * 1024

# --- Le jeu -----------------------------------------------------------------------------

MOD_ID = 'orvane'
PREFIXE = 'mon_orvane_'

# Les groupes de faune du jeu (0.I) qui reçoivent des lignées d'Orvane.
#   milieu : 'terre' = sans les lignées qui ne font que nager ; 'eau' = seulement elles ; 'tout'.
#   poids_jeu : la somme des poids du groupe dans le jeu 0.I ; elle règle la part d'Orvane.
# Les champs du jeu tirent aussi leur faune de GROUP_FOREST, mais bien moins que les forêts :
# tout ce groupe compte comme la forêt.
GROUPES = [
    {'groupe': 'GROUP_FOREST', 'biome': 'foret', 'milieu': 'terre', 'poids_jeu': 1653},
    {'groupe': 'GROUP_SWAMP', 'biome': 'marais', 'milieu': 'tout', 'poids_jeu': 840},
    {'groupe': 'GROUP_RIVER', 'biome': 'riviere', 'milieu': 'tout', 'poids_jeu': 575},
    {'groupe': 'GROUP_STREAM', 'biome': 'riviere', 'milieu': 'tout', 'poids_jeu': 92},
    {'groupe': 'GROUP_POND_ANIMAL', 'biome': 'lac', 'milieu': 'terre', 'poids_jeu': 260},
    {'groupe': 'GROUP_POND_FISH', 'biome': 'lac', 'milieu': 'eau', 'poids_jeu': 335},
    {'groupe': 'GROUP_CAVE', 'biome': 'grotte', 'milieu': 'terre', 'poids_jeu': 1360},
    {'groupe': 'GROUP_OCEAN_SHORE', 'biome': 'cote', 'milieu': 'eau', 'poids_jeu': 60},
]
BIOMES = sorted({g['biome'] for g in GROUPES})
OU_VIT = {'foret': 'dans les bois', 'marais': 'dans les marais', 'riviere': 'au bord des rivières',
          'lac': 'autour des étangs', 'grotte': 'dans les grottes', 'cote': 'le long de la côte'}

# Les modèles : un animal sauvage du jeu dont la créature prend la conduite, les attaques,
# la découpe et le dessin. Sa masse, sa vitesse, ses points de vie, son caractère, son nom
# et sa couleur viennent d'Orvane. Chaque liste va de la plus légère à la plus lourde :
# (masse maximale en kg, modèle), None pour « sans limite ».
MODELES = {
    ('quadrupede', 'herbivore'): [(0.3, 'mon_chipmunk'), (2, 'mon_squirrel'), (10, 'mon_groundhog'),
                                  (None, 'mon_deer')],
    ('quadrupede', 'omnivore'): [(0.5, 'mon_black_rat'), (5, 'mon_opossum'), (30, 'mon_raccoon'),
                                 (None, 'mon_boar_wild')],
    ('quadrupede', 'carnivore'): [(0.5, 'mon_weasel'), (3, 'mon_mink'), (8, 'mon_fox_red'),
                                  (20, 'mon_bobcat'), (40, 'mon_coyote'), (None, 'mon_cougar')],
    ('oiseau', '*'): [(0.2, 'mon_sparrow'), (1, 'mon_crow'), (4, 'mon_duck'), (None, 'mon_turkey')],
    ('serpent', 'carnivore'): [(0.4, 'mon_gartersnake'), (None, 'mon_rattlesnake')],
    ('serpent', '*'): [(None, 'mon_gartersnake')],
    ('hexapode', 'herbivore'): [(None, 'mon_grasshopper_small')],
    ('hexapode', 'omnivore'): [(None, 'mon_silverfish_small')],
    ('hexapode', 'carnivore'): [(None, 'mon_spider_jumping_small')],
    ('poisson', 'carnivore'): [(3, 'mon_fish_pike'), (None, 'mon_fish_muskellunge')],
    ('poisson', '*'): [(0.5, 'mon_fish_bluegill'), (5, 'mon_fish_carp'), (None, 'mon_fish_grass_carp')],
}
# Les modèles qui se reproduisent dans le jeu : leurs petits seront de la même lignée.
MODELES_FECONDS = {
    'mon_deer', 'mon_boar_wild', 'mon_coyote', 'mon_cougar', 'mon_sparrow', 'mon_crow', 'mon_duck',
    'mon_turkey', 'mon_gartersnake', 'mon_rattlesnake', 'mon_grasshopper_small',
    'mon_silverfish_small', 'mon_spider_jumping_small', 'mon_fish_pike', 'mon_fish_muskellunge',
    'mon_fish_bluegill', 'mon_fish_carp', 'mon_fish_grass_carp',
}

MODES_TERRE = {'marche', 'course', 'reptation', 'grimpe', 'saut', 'fouissage'}

# Les couleurs du terminal du jeu, et leur teinte approchée.
COULEURS = {
    'red': (170, 0, 0), 'green': (0, 120, 0), 'brown': (150, 95, 35), 'blue': (0, 0, 170),
    'magenta': (170, 0, 170), 'cyan': (0, 150, 150), 'light_gray': (190, 190, 190),
    'dark_gray': (95, 95, 95), 'light_red': (255, 85, 85), 'light_green': (85, 255, 85),
    'yellow': (255, 255, 85), 'light_blue': (85, 85, 255), 'pink': (255, 85, 255),
    'light_cyan': (85, 255, 255), 'white': (255, 255, 255),
}

REGIMES_FR = {'herbivore': 'herbivore', 'omnivore': 'omnivore', 'carnivore': 'carnivore',
              'filtreur': 'filtreur'}

# --- Petits outils ------------------------------------------------------------------------


def maintenant():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.strftime('%Y-%m-%dT%H:%M:%SZ')


def lire_json(chemin, defaut=None):
    try:
        with open(chemin, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return defaut


def ecrire_json(chemin, donnees, indent=2):
    """Écrit d'un coup : un fichier à moitié écrit ne remplace jamais le bon."""
    os.makedirs(os.path.dirname(chemin) or '.', exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(chemin) or '.', suffix='.tmp')
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(donnees, f, ensure_ascii=False, indent=indent)
        f.write('\n')
    os.chmod(tmp, 0o644)                    # mkstemp crée en 0600
    os.replace(tmp, chemin)


def court(texte, n=12):
    return hashlib.sha256(texte.encode('utf-8')).hexdigest()[:n]


# --- Les noms des créatures ------------------------------------------------------------------
# mon_orvane_<biome>_<lignée>, la lignée écrite sans « . » ni « ~ » (« vavator.savane~2 »
# devient « vavator_savane__2 ») : les identifiants du jeu restent sages, et le nom se relit.


def id_creature(biome, taxon):
    return PREFIXE + biome + '_' + taxon.replace('~', '__').replace('.', '_')


def relire_id(mid):
    if not mid.startswith(PREFIXE):
        return None
    reste = mid[len(PREFIXE):]
    biome, _, code = reste.partition('_')
    if biome not in BIOMES or not code:
        return None
    return biome, code.replace('__', '~').replace('_', '.')


# --- Lire le monde -----------------------------------------------------------------------------


def telecharger_json(url, delai=60):
    req = urllib.request.Request(url, headers={'Cache-Control': 'no-cache',
                                               'User-Agent': 'orvane-cataclysm/' + VERSION})
    with urllib.request.urlopen(req, timeout=delai) as r:
        return json.loads(r.read().decode('utf-8'))


def charger_monde(dossier_etat, instantane_local=None, generation0_locale=None, hors_ligne=False):
    """Rend (instantané, génération 0, d'où ils viennent). Garde une copie pour jouer hors ligne."""
    cache_inst = os.path.join(dossier_etat, 'cache', 'instantane.json')
    cache_g0 = os.path.join(dossier_etat, 'cache', 'generation-0.json')
    origine = 'fichier'
    if instantane_local:
        inst = lire_json(instantane_local)
    else:
        inst = None
        if not hors_ligne:
            try:
                inst = telecharger_json(MONDE_URL + 'instantane.json')
                origine = 'en ligne'
                ecrire_json(cache_inst, inst, indent=None)
            except (urllib.error.URLError, OSError, ValueError) as e:
                print('  le monde n\'a pas pu être lu en ligne (%s) : copie gardée' % e)
        if inst is None:
            inst = lire_json(cache_inst)
            origine = 'copie gardée'
        if inst is None:
            raise SystemExit('Aucun monde : pas de réseau et pas encore de copie gardée.')

    if generation0_locale:
        g0 = lire_json(generation0_locale)
    else:
        g0 = lire_json(cache_g0)
        # Un monde refait de zéro (autre naissance) a une autre génération 0.
        if (g0 is None or (g0.get('monde') or {}).get('ne') != inst['monde'].get('ne')) and not hors_ligne:
            try:
                g0 = telecharger_json(MONDE_URL + 'generation-0.json')
                ecrire_json(cache_g0, g0, indent=None)
            except (urllib.error.URLError, OSError, ValueError):
                g0 = g0 if g0 and (g0.get('monde') or {}).get('ne') == inst['monde'].get('ne') else None
    return inst, g0, origine


def vivantes(inst):
    """Les lignées animales vivantes (pas les machines, pas les plantes)."""
    for t in inst.get('taxons', []):
        if t.get('eteint') is not None:
            continue
        plan = t.get('plan') or {}
        if plan.get('regime') == 'synthetique' or t.get('famille', 'animale') != 'animale':
            continue
        yield t


def nageuse(t):
    """Une lignée d'eau : elle nage et n'a ni mode de terre ni vol."""
    loco = set((t.get('plan') or {}).get('locomotion') or [])
    return 'nage' in loco and not (loco & MODES_TERRE) and 'vol' not in loco


def du_milieu(t, milieu):
    if milieu == 'tout':
        return True
    return nageuse(t) == (milieu == 'eau')


def effectifs(inst, biome, milieu):
    """{taxon: effectif} dans un biome, pour les lignées de ce milieu."""
    out = {}
    for t in vivantes(inst):
        if not du_milieu(t, milieu):
            continue
        for p in t.get('populations', []):
            if p.get('biome') == biome and (p.get('effectif') or 0) > 0:
                out[t['id']] = out.get(t['id'], 0) + p['effectif']
    return out


# --- Traduire une lignée en créature du jeu --------------------------------------------------


def forme_de(t):
    plan = t.get('plan') or {}
    loco = set(plan.get('locomotion') or [])
    forme = (plan.get('corps') or {}).get('forme')
    if nageuse(t):
        return 'poisson'
    if forme == 'oiseau' or 'vol' in loco:
        return 'oiseau'
    if forme in ('serpent', 'hexapode'):
        return forme
    return 'quadrupede'


def modele_de(t):
    plan = t.get('plan') or {}
    forme, regime, masse = forme_de(t), plan.get('regime', 'herbivore'), plan.get('masse_kg') or 1
    for cle in ((forme, regime), (forme, '*'), (forme, 'herbivore'), ('quadrupede', regime),
                ('quadrupede', 'herbivore')):
        paliers = MODELES.get(cle)
        if paliers:
            for limite, modele in paliers:
                if limite is None or masse < limite:
                    return modele
    return 'mon_squirrel'


def couleur_de(t):
    rgb = (((t.get('plan') or {}).get('corps') or {}).get('couleurs') or {}).get('robe')
    if not rgb or len(rgb) < 3:
        return 'brown'
    return min(COULEURS, key=lambda c: sum((a - b) ** 2 for a, b in zip(COULEURS[c], rgb)))


def bornes(x, bas, haut):
    return max(bas, min(haut, x))


def creature(t, biome):
    """La fiche MONSTER d'une lignée dans un biome."""
    plan = t.get('plan') or {}
    masse = float(plan.get('masse_kg') or 1)
    regime = plan.get('regime', 'herbivore')
    temperament = float(plan.get('temperament') if plan.get('temperament') is not None else 0.5)
    vitesse = float(plan.get('vitesse_rel') or 5)
    modele = modele_de(t)
    mid = id_creature(biome, t['id'])
    nom = t.get('nom_commun') or t.get('nom') or t['id']
    grammes = max(1, round(masse * 1000))

    # temperament : 0 fuit toujours, 1 riposte toujours. Un herbivore fuit quoi qu'il arrive ;
    # un carnivore hardi (au-delà de 0,75) attaque.
    if regime == 'carnivore':
        agressivite = round(-20 + temperament * 40)
    elif regime == 'omnivore':
        agressivite = round(-40 + temperament * 50)
    else:
        agressivite = round(-99 + temperament * 60)
    moral = round(-20 + temperament * 80)

    masse_txt = ('%g kg' % round(masse, 2)) if masse >= 1 else ('%d g' % grammes)
    fiche = {
        'type': 'MONSTER',
        'id': mid,
        'copy-from': modele,
        'looks_like': modele,
        'name': {'str_sp': nom},
        'description': (
            "Une lignée du monde d'Orvane : %s, %s. Celle-ci vit %s. "
            "Ce que vous faites à ces bêtes compte dans le monde partagé d'Orvane : "
            "chacune que vous tuez y est renvoyée."
            % (REGIMES_FR.get(regime, regime), masse_txt, OU_VIT.get(biome, biome))),
        'weight': '%d g' % grammes,
        'volume': '%d ml' % grammes,
        'hp': max(1, round(10 * masse ** 0.4)),
        'speed': int(bornes(round(40 + 25 * vitesse), 30, 350)),
        'aggression': int(bornes(agressivite, -99, 100)),
        'morale': int(bornes(moral, -99, 100)),
        'color': couleur_de(t),
        'categories': ['WILDLIFE'],
        'upgrades': False,
    }
    if modele in MODELES_FECONDS:
        fiche['reproduction'] = {'baby_type': {'baby_monster': mid}}
    return fiche


def taille_groupe(t):
    s = float((t.get('plan') or {}).get('sociabilite') or 0)
    haut = 1 + round(s * 4)
    return [2 if s >= 0.6 else 1, max(haut, 2 if s >= 0.6 else 1)]


def construire_mod(inst, g0, part, connues):
    """Rend (créatures, groupes, résumé). « connues » garde chaque créature déjà écrite,
    pour qu'une sauvegarde qui en contient une se charge encore quand sa lignée s'éteint."""
    if not 0 < part < 1:
        raise SystemExit('La part d\'Orvane doit être entre 0 et 1 (exclus).')
    par_id = {t['id']: t for t in vivantes(inst)}
    ne = inst['monde'].get('ne')
    nouvelles = {}
    groupes = []
    resume = []
    sous_groupes = {}

    for g in GROUPES:
        cle = (g['biome'], g['milieu'])
        if cle not in sous_groupes:
            eff = effectifs(inst, *cle)
            total = sum(eff.values())
            membres = []
            for taxon, e in sorted(eff.items(), key=lambda kv: (-kv[1], kv[0])):
                poids = round(1000 * e / total) if total else 0
                if poids < 1:
                    continue           # trop rare pour paraître : il ne se montre pas
                t = par_id[taxon]
                fiche = creature(t, g['biome'])
                nouvelles[fiche['id']] = fiche
                membres.append({'monster': fiche['id'], 'weight': poids, 'pack_size': taille_groupe(t)})
            ratio = 1.0
            if g0:
                avant = sum(effectifs(g0, *cle).values())
                if avant > 0:
                    ratio = bornes(total / avant, 0.0, 3.0)
            sid = 'GROUP_ORVANE_%s_%s' % (g['biome'].upper(), g['milieu'].upper())
            sous_groupes[cle] = (sid, membres, ratio)
            if membres:
                groupes.append({'type': 'monstergroup', 'id': sid, 'default': 'mon_null',
                                'is_animal': True, 'monsters': membres})
        sid, membres, ratio = sous_groupes[cle]
        poids = round(g['poids_jeu'] * part / (1 - part) * ratio)
        if membres and poids >= 1:
            # Compléter un groupe du jeu sans le remplacer. Le jeu remet is_animal à faux
            # quand on ne le redit pas : on le redit.
            groupes.append({'type': 'monstergroup', 'id': g['groupe'], 'is_animal': True,
                            'monsters': [{'group': sid, 'weight': poids}]})
        resume.append((g['groupe'], g['biome'], len(membres), poids, ratio))

    for mid, fiche in nouvelles.items():
        connues[mid] = {'fiche': fiche, 'ne': ne}
    creatures = [connues[mid]['fiche'] for mid in sorted(connues)]
    return creatures, groupes, resume


def modinfo(inst):
    cycle = inst.get('cycle', {}).get('n')
    return [{
        'type': 'MOD_INFO',
        'id': MOD_ID,
        'name': "Orvane : la faune d'un monde vivant",
        'authors': ['Orvane'],
        'description': (
            "Ajoute à la faune sauvage les lignées du monde d'Orvane (cycle %s), un monde que "
            "partagent plusieurs jeux. Les bêtes que vous tuez y sont renvoyées par la passerelle : "
            "une espèce trop chassée s'y raréfie, dans tous les jeux à la fois." % cycle),
        'category': 'creatures',
        'dependencies': ['dda'],
        'version': 'cycle %s' % cycle,
    }]


# --- Le dossier du jeu ---------------------------------------------------------------------------


def dossier_cdda(arg):
    """Le dossier « utilisateur » du jeu : celui qui contient save/, mods/ et config/.
    Sous Windows, c'est le dossier du jeu lui-même."""
    if arg:
        return os.path.abspath(os.path.expanduser(arg))
    candidats = []
    if os.environ.get('LOCALAPPDATA'):
        candidats.append(os.path.join(os.environ['LOCALAPPDATA'], 'cataclysm-dda'))
    home = os.path.expanduser('~')
    candidats += [os.path.join(home, '.cataclysm-dda'),
                  os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.join(home, '.local', 'share'),
                               'cataclysm-dda'),
                  os.path.join(home, 'Library', 'Application Support', 'Cataclysm')]
    for c in candidats:
        if os.path.isdir(os.path.join(c, 'save')):
            return c
    raise SystemExit('Dossier du jeu introuvable : indiquez-le avec --cdda (celui qui contient save/).')


def mondes_du_jeu(cdda):
    """{nom du monde: dossier}."""
    racine = os.path.join(cdda, 'save')
    if not os.path.isdir(racine):
        return {}
    return {n: os.path.join(racine, n) for n in sorted(os.listdir(racine))
            if os.path.isfile(os.path.join(racine, n, 'mods.json'))}


def mods_du_monde(dossier):
    return lire_json(os.path.join(dossier, 'mods.json'), []) or []


def activer(dossier):
    mods = mods_du_monde(dossier)
    if MOD_ID in mods:
        return False
    ecrire_json(os.path.join(dossier, 'mods.json'), mods + [MOD_ID])
    return True


# --- Lire une sauvegarde --------------------------------------------------------------------------
# Une sauvegarde est « <nom>.sav » (du JSON, après une ligne « # version ») ou, dans un monde
# compressé, « <nom>.sav.zzip » : des trames zstd à la suite, chacune précédée d'une trame
# « sautable » qui porte son nom. L'archive ne fait qu'ajouter : la dernière trame d'un nom
# est la bonne (src/zzip.h du jeu).

ZSTD_MAGIC = 0xFD2FB528
SAUTABLE_MIN, SAUTABLE_MAX = 0x184D2A50, 0x184D2A5F
SAUTABLE_NOM = 0


def longueur_trame(b, pos):
    """La longueur d'une trame zstd (RFC 8878), sans la décompresser."""
    fhd = b[pos + 4]
    fcs, seul, somme, dico = fhd >> 6, (fhd >> 5) & 1, (fhd >> 2) & 1, fhd & 3
    p = pos + 5 + (0 if seul else 1) + (0, 1, 2, 4)[dico] + ((1 if seul else 0), 2, 4, 8)[fcs]
    while True:
        if p + 3 > len(b):
            raise ValueError('trame zstd tronquée')
        h = b[p] | (b[p + 1] << 8) | (b[p + 2] << 16)
        p += 3
        dernier, genre, taille = h & 1, (h >> 1) & 3, h >> 3
        if genre in (0, 2):
            p += taille
        elif genre == 1:
            p += 1
        else:
            raise ValueError('bloc zstd réservé')
        if dernier:
            break
    return p + (4 if somme else 0) - pos


def entrees_zzip(donnees):
    """[(nom, trame compressée)], dans l'ordre de l'archive."""
    pos, nom, out = 0, None, []
    while pos + 8 <= len(donnees):
        magie = struct.unpack_from('<I', donnees, pos)[0]
        if SAUTABLE_MIN <= magie <= SAUTABLE_MAX:
            taille = struct.unpack_from('<I', donnees, pos + 4)[0]
            if magie - SAUTABLE_MIN == SAUTABLE_NOM:
                nom = donnees[pos + 8:pos + 8 + taille].decode('utf-8', 'replace')
            pos += 8 + taille
        elif magie == ZSTD_MAGIC:
            n = longueur_trame(donnees, pos)
            out.append((nom, donnees[pos:pos + n]))
            nom, pos = None, pos + n
        else:
            break          # le remplissage, puis l'index de fin d'archive
    return out


def _zstd_ctypes():
    noms = [ctypes.util.find_library('zstd'), 'libzstd.so.1', 'libzstd.dylib', 'libzstd.dll', 'zstd.dll']
    for nom in [n for n in noms if n]:
        try:
            lib = ctypes.CDLL(nom)
        except OSError:
            continue
        lib.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
        lib.ZSTD_getFrameContentSize.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        lib.ZSTD_decompress.restype = ctypes.c_size_t
        lib.ZSTD_decompress.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t]
        lib.ZSTD_isError.restype = ctypes.c_uint
        lib.ZSTD_isError.argtypes = [ctypes.c_size_t]

        def decompresser(trame, lib=lib):
            taille = lib.ZSTD_getFrameContentSize(trame, len(trame))
            if taille >= (1 << 64) - 2:           # inconnue ou erreur
                taille = 1 << 28
            dst = ctypes.create_string_buffer(int(taille) or 1)
            n = lib.ZSTD_decompress(dst, len(dst), trame, len(trame))
            if lib.ZSTD_isError(n):
                raise ValueError('trame zstd illisible')
            return dst.raw[:n]
        return decompresser
    return None


def decompresseur_zstd():
    try:
        from compression import zstd as _z            # Python 3.14
        return _z.decompress
    except ImportError:
        pass
    try:
        import zstandard as _z
        return lambda b: _z.ZstdDecompressor().decompress(b, max_output_size=1 << 28)
    except ImportError:
        pass
    try:
        import pyzstd as _z
        return _z.decompress
    except ImportError:
        pass
    d = _zstd_ctypes()
    if d:
        return d
    exe = shutil.which('zstd')
    if exe:
        return lambda b: subprocess.run([exe, '-d', '-c', '-q'], input=b, capture_output=True,
                                        check=True).stdout
    return None


def lire_sauvegarde(chemin, decompresser=None):
    """Le JSON d'une sauvegarde de personnage (.sav ou .sav.zzip)."""
    with open(chemin, 'rb') as f:
        brut = f.read()
    if chemin.endswith('.zzip'):
        decompresser = decompresser or decompresseur_zstd()
        if decompresser is None:
            raise SystemExit(
                'Cette sauvegarde est compressée, et aucun décompresseur zstd n\'est installé.\n'
                'Installez-en un (pip install zstandard), ou créez un monde avec l\'option '
                '« World data compression » désactivée.')
        voulu = os.path.basename(chemin)[:-len('.zzip')]
        trames = [tr for nom, tr in entrees_zzip(brut) if nom == voulu]
        if not trames:
            trames = [tr for nom, tr in entrees_zzip(brut) if (nom or '').endswith('.sav')]
        if not trames:
            raise ValueError('pas de sauvegarde dans %s' % chemin)
        brut = decompresser(trames[-1])
    texte = brut.decode('utf-8')
    while texte.startswith('#'):                      # « # version 36 »
        texte = texte.split('\n', 1)[1] if '\n' in texte else ''
    return json.loads(texte)


def tues(sauvegarde):
    """{id de créature: nombre} des bêtes tuées par le personnage et ses compagnons."""
    kt = sauvegarde.get('kill_tracker')
    if not isinstance(kt, dict):
        return {}
    return {k: int(v) for k, v in (kt.get('kills') or {}).items()}


def sauvegardes(dossier_monde):
    out = []
    for n in sorted(os.listdir(dossier_monde)):
        if n.endswith('.sav') or n.endswith('.sav.zzip'):
            out.append(os.path.join(dossier_monde, n))
    return out


# --- Les lots ----------------------------------------------------------------------------------


def verifier_lot(lot):
    """Ce que le Supabase d'Orvane refuserait (le détail : node outils/verifier.js)."""
    erreurs = []
    for cle in ('orvane', 'monde', 'source', 'periode', 'evenements'):
        if cle not in lot:
            erreurs.append('il manque « %s »' % cle)
    ev = lot.get('evenements') or []
    if len(ev) > MAX_EVENEMENTS:
        erreurs.append('plus de %d événements' % MAX_EVENEMENTS)
    if len(json.dumps(lot, ensure_ascii=False).encode('utf-8')) > MAX_OCTETS:
        erreurs.append('plus de 64 Ko')
    for i, e in enumerate(ev):
        if e.get('action') != 'prelevement':
            erreurs.append('evenements[%d] : action inattendue' % i)
        if not isinstance(e.get('n'), int) or not 0 <= e['n'] <= 10000:
            erreurs.append('evenements[%d] : n doit être un nombre de 0 à 10 000' % i)
        if not isinstance(e.get('biome'), str) or not isinstance(e.get('taxon'), str):
            erreurs.append('evenements[%d] : biome et taxon sont des textes' % i)
    return erreurs


def faire_lots(etat, nom_monde, comptes, debut, fin):
    """Des lots de 500 événements au plus."""
    evenements = [{'taxon': taxon, 'biome': biome, 'action': 'prelevement', 'n': min(n, 10000)}
                  for (taxon, biome), n in sorted(comptes.items()) if n > 0]
    lots = []
    for i in range(0, len(evenements), MAX_EVENEMENTS):
        lots.append({
            'orvane': '0.1',
            'monde': etat.get('monde') or 'orvane',
            'familles': ['animale'],
            'instantane': etat.get('cycle'),
            'source': {'jeu': JEU, 'compte': etat['compte'], 'zone': 'monde-cdda:' + court(nom_monde)},
            'periode': {'debut': debut, 'fin': fin},
            'evenements': evenements[i:i + MAX_EVENEMENTS],
        })
    return lots


def deposer(lot, compte, cle=CLE_JEU, delai=60):
    corps = json.dumps({'p_cle': cle, 'p_compte': compte, 'p_lot': lot}).encode('utf-8')
    req = urllib.request.Request(SUPABASE + '/rest/v1/rpc/orvane_deposer', data=corps, method='POST',
                                 headers={'apikey': CLE_PROJET, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=delai) as r:
        return json.loads(r.read().decode('utf-8'))


# Ces refus passent avec le temps : le lot attend. Les autres veulent dire que le lot est faux.
REFUS_PASSAGERS = ('trop de dépôts', 'quota', 'suspendu', 'clé de jeu inconnue')

# --- L'état de la passerelle -------------------------------------------------------------------


def dossier_etat(cdda, arg=None):
    return os.path.abspath(arg) if arg else os.path.join(cdda, 'orvane-passerelle')


def charger_etat(dossier):
    etat = lire_json(os.path.join(dossier, 'etat.json'), {}) or {}
    if not etat.get('joueur'):
        etat['joueur'] = str(uuid.uuid4())          # reste sur cet ordinateur
    # Orvane ne reçoit qu'une empreinte : le joueur reste anonyme.
    etat['compte'] = 'h:' + hashlib.sha256(etat['joueur'].encode()).hexdigest()[:24]
    etat.setdefault('connues', {})
    etat.setdefault('tues', {})
    etat.setdefault('comptes', {})
    return etat


def garder_etat(dossier, etat):
    ecrire_json(os.path.join(dossier, 'etat.json'), etat)


# --- Les commandes -------------------------------------------------------------------------------


def cmd_preparer(a):
    cdda = dossier_cdda(a.cdda)
    de = dossier_etat(cdda, a.etat)
    etat = charger_etat(de)
    inst, g0, origine = charger_monde(de, a.instantane, a.generation_0, a.hors_ligne)
    if etat.get('ne') and etat['ne'] != inst['monde'].get('ne'):
        print('  le monde d\'Orvane a été refait de zéro : les anciennes lignées restent définies, '
              'mais leurs bêtes ne seront plus renvoyées')
    creatures, groupes, resume = construire_mod(inst, g0, a.part, etat['connues'])

    dossier_mod = os.path.join(cdda, 'mods', MOD_ID)
    ecrire_json(os.path.join(dossier_mod, 'modinfo.json'), modinfo(inst))
    ecrire_json(os.path.join(dossier_mod, 'creatures.json'), creatures)
    ecrire_json(os.path.join(dossier_mod, 'groupes.json'), groupes)

    etat.update({'monde': inst['monde'].get('id', 'orvane'), 'ne': inst['monde'].get('ne'),
                 'cycle': inst.get('cycle', {}).get('n'), 'prepare': iso(maintenant()),
                 'part': a.part})
    garder_etat(de, etat)

    print('Monde d\'Orvane : cycle %s (%s)' % (etat['cycle'], origine))
    for groupe, biome, n, poids, ratio in resume:
        print('  %-18s %-8s %3d lignées  poids %4d  (effectif x%.2f depuis la genèse)'
              % (groupe, biome, n, poids, ratio))
    print('Mod écrit : %s (%d créatures)' % (dossier_mod, len(creatures)))

    mondes = mondes_du_jeu(cdda)
    voulus = list(mondes) if a.activer_tous else (a.activer or [])
    for nom in voulus:
        if nom not in mondes:
            print('  monde « %s » introuvable dans %s' % (nom, os.path.join(cdda, 'save')))
        elif activer(mondes[nom]):
            print('  mod activé dans le monde « %s »' % nom)
    sans = [n for n, d in mondes.items() if MOD_ID not in mods_du_monde(d)]
    if sans:
        print('  sans le mod : %s (--activer NOM pour l\'ajouter ; un nouveau monde : '
              'cocher « Orvane » dans la liste des mods)' % ', '.join(sans))


def cmd_compter(a):
    cdda = dossier_cdda(a.cdda)
    de = dossier_etat(cdda, a.etat)
    etat = charger_etat(de)
    if not etat.get('cycle'):
        raise SystemExit('Lancez d\'abord « preparer » : la passerelle ne sait pas encore quel monde '
                         'le jeu utilise.')
    decompresser = None
    fin = iso(maintenant())
    sortie = os.path.join(de, 'essais' if a.essai else 'lots')
    total_lots = 0
    for nom_monde, dossier in mondes_du_jeu(cdda).items():
        if MOD_ID not in mods_du_monde(dossier):
            continue
        comptes = {}
        for chemin in sauvegardes(dossier):
            if chemin.endswith('.zzip') and decompresser is None:
                decompresser = decompresseur_zstd()
            try:
                donnees = lire_sauvegarde(chemin, decompresser)
            except (ValueError, OSError) as e:
                print('  %s : illisible (%s), ignorée' % (os.path.basename(chemin), e))
                continue
            cle = nom_monde + '/' + os.path.basename(chemin).replace('.zzip', '')
            avant = etat['tues'].get(cle, {})
            apres = {}
            for mid, n in tues(donnees).items():
                if not mid.startswith(PREFIXE):
                    continue            # zombies, humains, robots, faune du jeu : rien à renvoyer
                apres[mid] = n
                delta = n - avant.get(mid, 0)
                if delta <= 0:
                    continue            # une sauvegarde plus ancienne a été rechargée
                connue = etat['connues'].get(mid)
                if connue and connue.get('ne') != etat.get('ne'):
                    continue            # une lignée d'un monde refait de zéro
                lu = relire_id(mid)
                if not lu:
                    continue
                biome, taxon = lu
                comptes[(taxon, biome)] = comptes.get((taxon, biome), 0) + delta
            if not a.essai:
                etat['tues'][cle] = apres
        debut = etat['comptes'].get(nom_monde) or etat.get('prepare') or fin
        if comptes:
            for i, lot in enumerate(faire_lots(etat, nom_monde, comptes, debut, fin)):
                erreurs = verifier_lot(lot)
                if erreurs:
                    raise SystemExit('Lot refusé avant envoi : ' + '; '.join(erreurs))
                # Daté, puis unique : deux comptes dans la même seconde ne s'écrasent pas.
                nom = '%s-%s-%s.json' % (fin.replace(':', '').replace('-', ''), court(nom_monde, 8),
                                         uuid.uuid4().hex[:8])
                ecrire_json(os.path.join(sortie, nom), lot)
                total_lots += 1
            n = sum(comptes.values())
            print('Monde « %s » : %d bêtes d\'Orvane tuées depuis le dernier compte (%d lignées)'
                  % (nom_monde, n, len(comptes)))
        else:
            print('Monde « %s » : aucune bête d\'Orvane tuée depuis le dernier compte' % nom_monde)
        if not a.essai:
            etat['comptes'][nom_monde] = fin
    if not a.essai:
        garder_etat(de, etat)
    if a.essai:
        print('Essai : %d lot(s) dans %s, qui ne seront jamais envoyés.' % (total_lots, sortie))
    else:
        print('%d lot(s) en attente dans %s' % (total_lots, sortie))


def cmd_envoyer(a):
    cdda = dossier_cdda(a.cdda)
    de = dossier_etat(cdda, a.etat)
    etat = charger_etat(de)
    attente = os.path.join(de, 'lots')
    fichiers = sorted(f for f in os.listdir(attente) if f.endswith('.json')) if os.path.isdir(attente) else []
    if not fichiers:
        print('Aucun lot en attente.')
        return
    envoyes = 0
    for f in fichiers:
        chemin = os.path.join(attente, f)
        lot = lire_json(chemin)
        try:
            rep = deposer(lot, etat['compte'])
        except (urllib.error.URLError, OSError, ValueError) as e:
            print('Orvane injoignable (%s) : les lots attendent le prochain envoi.' % e)
            break
        if rep.get('ok'):
            os.makedirs(os.path.join(de, 'envoyes'), exist_ok=True)
            os.replace(chemin, os.path.join(de, 'envoyes', f))
            envoyes += 1
            continue
        erreur = rep.get('erreur', '?')
        if any(m in erreur for m in REFUS_PASSAGERS):
            print('Orvane dit « %s » : les lots attendent.' % erreur)
            break
        os.makedirs(os.path.join(de, 'refuses'), exist_ok=True)
        os.replace(chemin, os.path.join(de, 'refuses', f))
        ecrire_json(os.path.join(de, 'refuses', f + '.erreur.json'), rep)
        print('Lot %s refusé : %s (mis de côté dans refuses/)' % (f, erreur))
    etat['envoi'] = iso(maintenant())
    garder_etat(de, etat)
    print('%d lot(s) déposé(s) dans Orvane.' % envoyes)


def cmd_apres(a):
    cmd_compter(a)
    if not a.essai:
        cmd_envoyer(a)


def cmd_etat(a):
    cdda = dossier_cdda(a.cdda)
    de = dossier_etat(cdda, a.etat)
    etat = charger_etat(de)
    print('Dossier du jeu : %s' % cdda)
    print('Monde d\'Orvane : cycle %s, préparé le %s' % (etat.get('cycle'), etat.get('prepare')))
    print('Créatures définies : %d' % len(etat['connues']))
    for nom, d in mondes_du_jeu(cdda).items():
        print('  monde « %s » : %s, dernier compte %s'
              % (nom, 'mod actif' if MOD_ID in mods_du_monde(d) else 'sans le mod',
                 etat['comptes'].get(nom, 'jamais')))
    for sous in ('lots', 'envoyes', 'refuses'):
        p = os.path.join(de, sous)
        n = len([f for f in os.listdir(p) if f.endswith('.json') and not f.endswith('.erreur.json')]) \
            if os.path.isdir(p) else 0
        print('Lots %-8s : %d' % (sous.replace('lots', 'en attente'), n))
    print('Dernier envoi : %s' % etat.get('envoi', 'jamais'))


def executer(argv):
    """Pour un programme qui embarque la passerelle (le lanceur) : lance une commande, comme en
    ligne de commande, et rend (réussite, ce qu'elle a dit). Ne lève jamais d'exception."""
    import io
    from contextlib import redirect_stdout
    tampon = io.StringIO()
    reussi = True
    with redirect_stdout(tampon):
        try:
            main(list(argv))
        except SystemExit as e:
            if e.code not in (None, 0):
                reussi = False
                if not isinstance(e.code, int):
                    print(e.code)
        except Exception as e:                   # le programme hôte ne doit pas tomber
            reussi = False
            print('erreur inattendue : %r' % (e,))
    return reussi, tampon.getvalue().strip()


def main(argv=None):
    p = argparse.ArgumentParser(description='Passerelle Orvane pour Cataclysm: Dark Days Ahead')
    p.add_argument('--cdda', help='dossier du jeu qui contient save/ (sous Windows : le dossier du jeu)')
    p.add_argument('--etat', help='où la passerelle garde ses fichiers (défaut : <cdda>/orvane-passerelle)')
    sp = p.add_subparsers(dest='commande', required=True)

    pr = sp.add_parser('preparer', help='écrire le mod depuis le monde du jour (avant de jouer)')
    pr.add_argument('--part', type=float, default=0.35,
                    help="part des lignées d'Orvane dans la faune sauvage (défaut 0,35)")
    pr.add_argument('--activer', action='append', metavar='MONDE', help='activer le mod dans ce monde')
    pr.add_argument('--activer-tous', action='store_true', help='activer le mod dans tous les mondes')
    pr.add_argument('--instantane', help='lire le monde dans ce fichier plutôt qu\'en ligne')
    pr.add_argument('--generation-0', help='la génération 0 dans ce fichier plutôt qu\'en ligne')
    pr.add_argument('--hors-ligne', action='store_true', help='ne rien télécharger')
    pr.set_defaults(f=cmd_preparer)

    for nom, f, aide in (('compter', cmd_compter, 'compter les bêtes tuées (après avoir joué)'),
                         ('apres', cmd_apres, 'compter puis envoyer')):
        c = sp.add_parser(nom, help=aide)
        c.add_argument('--essai', action='store_true',
                       help='écrire les lots dans essais/ sans jamais les envoyer, et ne rien retenir')
        c.set_defaults(f=f)
    sp.add_parser('envoyer', help='déposer les lots en attente dans Orvane').set_defaults(f=cmd_envoyer)
    sp.add_parser('etat', help='où en est la passerelle').set_defaults(f=cmd_etat)

    a = p.parse_args(argv)
    a.f(a)


if __name__ == '__main__':
    main()
