"""Essai de la partie Orvane du lanceur, depuis le code source.

La fabrication automatique le lance sous Windows avant de fabriquer l'exécutable : les vrais
réglages du lanceur, le vrai monde d'Orvane (en ligne), une sauvegarde compressée comme celles
du jeu. Le mode essai est coché : rien n'est envoyé à Orvane.

    python tests/orvane_essai.py
"""

import json
import os
import shutil
import struct
import sys
import tempfile

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)

TMP = tempfile.mkdtemp(prefix='orvane-essai-')
# Les réglages du lanceur vont dans %LOCALAPPDATA% : ceux de l'essai restent à part.
os.environ['LOCALAPPDATA'] = os.path.join(TMP, 'appdata')
os.makedirs(os.environ['LOCALAPPDATA'])

from PySide6.QtCore import QCoreApplication  # noqa: E402

APP = QCoreApplication([])

from cddagl.sql.functions import init_config, set_config_value  # noqa: E402

init_config(RACINE)

from cddagl import orvane  # noqa: E402
from cddagl.orvane import passerelle  # noqa: E402

echecs = []


def ok(condition, message):
    print(('  OK    ' if condition else '  ECHEC ') + message)
    if not condition:
        echecs.append(message)


def faire(commandes):
    """Le travail de la passerelle, dans ce fil : le signal arrive tout de suite."""
    recu = []
    travail = orvane.Travail(commandes)
    travail.fini.connect(lambda reussi, texte: recu.append((reussi, texte)))
    travail.run()
    return recu[0] if recu else (False, 'aucun signal')


def zzip(nom, contenu):
    """Une archive comme celles du jeu (src/zzip.h) : une ancienne version de la fiche,
    puis la bonne, compressées par zstd."""
    import zstandard

    def sautable(variante, charge):
        return struct.pack('<II', passerelle.SAUTABLE_MIN + variante, len(charge)) + charge

    out = sautable(15, b'\0' * 12)
    for c in (b'# version 36\n{"kill_tracker": {"kills": {}, "npc_kills": []}}', contenu):
        trame = zstandard.ZstdCompressor(write_content_size=True).compress(c)
        out += sautable(0, nom.encode()) + sautable(1, b'\x11' * 8) + trame
    return out + b'\0' * 200 + b'index de fin'


try:
    jeu = os.path.join(TMP, 'Cataclysm')
    os.makedirs(os.path.join(jeu, 'save', 'Monde'))
    with open(os.path.join(jeu, 'save', 'Monde', 'mods.json'), 'w') as f:
        f.write('["dda"]')
    exe = os.path.join(jeu, 'cataclysm-tiles.exe')
    open(exe, 'w').close()

    print('Les réglages')
    ok(not orvane.actif(), 'Orvane est désactivé par défaut')
    set_config_value('orvane.enabled', 'True')
    set_config_value('orvane.test_mode', 'True')
    set_config_value('orvane.all_worlds', 'True')
    ok(orvane.actif() and orvane.essai() and orvane.tous_les_mondes(), 'les cases cochées se relisent')
    cdda = orvane.dossier_utilisateur(jeu, exe)
    ok(cdda == jeu, 'le dossier du jeu est celui de son exécutable')

    print('Avant la partie')
    reussi, texte = faire(orvane.commandes_avant(cdda))
    print('    ' + texte.replace('\n', '\n    '))
    ok(reussi, 'preparer lit le monde en ligne et écrit le mod')
    with open(os.path.join(jeu, 'mods', 'orvane', 'creatures.json'), encoding='utf-8') as f:
        creatures = json.load(f)
    ok(len(creatures) > 20, '%d créatures dans le mod' % len(creatures))
    with open(os.path.join(jeu, 'save', 'Monde', 'mods.json')) as f:
        ok('orvane' in json.load(f), 'le mod est activé dans le monde')

    print('Après la partie')
    x, y = creatures[0]['id'], creatures[1]['id']
    with open(os.path.join(jeu, 'save', 'Monde', '#RQ==.sav.zzip'), 'wb') as f:
        f.write(zzip('#RQ==.sav', ('# version 36\n' + json.dumps(
            {'kill_tracker': {'kills': {x: 3, 'mon_zombie': 12}, 'npc_kills': []}})).encode()))
    with open(os.path.join(jeu, 'save', 'Monde', '#Rg==.sav'), 'w', encoding='utf-8') as f:
        f.write('# version 36\n' + json.dumps({'kill_tracker': {'kills': {y: 1}, 'npc_kills': []}}))
    commandes = orvane.commandes_apres(cdda)
    ok(commandes == [['--cdda', cdda, 'apres', '--essai']], 'en mode essai : apres --essai')
    reussi, texte = faire(commandes)
    print('    ' + texte.replace('\n', '\n    '))
    ok(reussi, 'le compte réussit, sauvegarde compressée comprise')
    dossier = os.path.join(jeu, 'orvane-passerelle')
    lots = [json.load(open(os.path.join(dossier, 'essais', n), encoding='utf-8'))
            for n in os.listdir(os.path.join(dossier, 'essais'))]
    ok(sum(e['n'] for l in lots for e in l['evenements']) == 4, '4 bêtes d\'Orvane comptées, pas les zombies')
    ok(not os.path.isdir(os.path.join(dossier, 'lots')), 'rien en attente d\'envoi')
    ok('cycle' in (orvane.etat(cdda) or ''), 'l\'état se lit pour l\'onglet des réglages')
    ok([c[2] for c in orvane.commandes_avant(cdda)] == ['apres', 'preparer'],
       'au lancement suivant : rattraper le compte, puis preparer')
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print('Tout passe.' if not echecs else '%d échec(s).' % len(echecs))
sys.exit(1 if echecs else 0)
