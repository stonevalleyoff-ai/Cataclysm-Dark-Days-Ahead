"""Orvane : brancher le jeu sur le monde partagé d'Orvane.

Orvane (https://github.com/stonevalleyoff-ai/orvane) fait vivre un monde que plusieurs jeux
partagent. La passerelle (passerelle.py, copiée telle quelle du dépôt d'Orvane, sous sa propre
licence) écrit avant chaque partie le mod « orvane », qui ajoute les lignées de ce monde à la
faune sauvage, et compte après la partie les bêtes d'Orvane tuées, qu'elle envoie à Orvane.

Ce module relie la passerelle au lanceur : les réglages, le dossier du jeu, et un fil d'exécution
pour que le lanceur ne se fige pas pendant que la passerelle lit le réseau.
"""

import logging
import os

from PySide6.QtCore import QThread, Signal

from cddagl.orvane import passerelle
from cddagl.sql.functions import get_config_value, config_true

logger = logging.getLogger('cddagl')


def actif():
    return config_true(get_config_value('orvane.enabled', 'False'))


def essai():
    return config_true(get_config_value('orvane.test_mode', 'False'))


def tous_les_mondes():
    return config_true(get_config_value('orvane.all_worlds', 'False'))


def dossier_utilisateur(game_dir, exe_path):
    """Le dossier où le jeu range save/, mods/ et config/ : la session choisie dans le lanceur,
    sinon le dossier de l'exécutable (le jeu y est lancé, et c'est son dossier par défaut)."""
    session = get_config_value('session_directory')
    if session is not None and session != game_dir:
        return session
    if exe_path:
        return os.path.dirname(exe_path)
    return game_dir


def _etat_existe(cdda):
    return os.path.isfile(os.path.join(cdda, 'orvane-passerelle', 'etat.json'))


def _apres(cdda):
    return ['--cdda', cdda, 'apres'] + (['--essai'] if essai() else [])


def commandes_avant(cdda):
    """Avant la partie : d'abord compter une partie que le lanceur n'a pas vue finir (il était
    fermé), puis écrire le mod depuis le monde du jour."""
    commandes = []
    if _etat_existe(cdda):
        commandes.append(_apres(cdda))
    commandes.append(['--cdda', cdda, 'preparer'] + (['--activer-tous'] if tous_les_mondes() else []))
    return commandes


def commandes_apres(cdda):
    """Après la partie : compter les bêtes tuées et les envoyer."""
    if not _etat_existe(cdda):
        return []
    return [_apres(cdda)]


def etat(cdda):
    """Le texte de « passerelle etat », ou None si la passerelle n'a jamais servi ici."""
    if not cdda or not _etat_existe(cdda):
        return None
    reussi, texte = passerelle.executer(['--cdda', cdda, 'etat'])
    return texte if reussi else None


def resume(texte):
    """La dernière ligne de ce qu'a dit la passerelle : c'est son bilan."""
    lignes = [l.strip() for l in (texte or '').splitlines() if l.strip()]
    return lignes[-1] if lignes else ''


class Travail(QThread):
    """Lance des commandes de la passerelle l'une après l'autre, hors du fil de l'interface."""

    fini = Signal(bool, str)

    def __init__(self, commandes, parent=None):
        super(Travail, self).__init__(parent)
        self.commandes = commandes

    def run(self):
        reussi, textes = True, []
        for argv in self.commandes:
            ok, texte = passerelle.executer(argv)
            reussi = reussi and ok
            if texte:
                textes.append(texte)
            logger.info('Orvane (%s) : %s', argv[2], texte)
        self.fini.emit(reussi, '\n'.join(textes))
