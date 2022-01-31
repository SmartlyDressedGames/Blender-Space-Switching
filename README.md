# Blender Space Switching Add-on

Not perfect, but potentially useful for some people.

[Example / Demo View](https://youtu.be/t-Yf6Mew1ZI)

## Installation

1. Download the newest space_switching.zip from https://github.com/SmartlyDressedGames/Blender-Space-Switching/releases
2. In Blender navgiate to Edit > Preferences > Add-ons > Install...
3. Select the zip to install

## History

This add-on is based upon some of the lessons from [Richard Lico's Space Switching Course](https://www.animationsherpa.com/courses/space-switching). In late 2020 I made a personal add-on to automate these techniques in Blender, but it assumed the armatures were local to the file so only useful for scripted rigs. [Pierrick Picaut's video](https://www.youtube.com/watch?v=Ut8KCfYKuzc) does a better job explaining the technique in Blender than I could, but it is a hassle to constantly change between Pose Mode and Object Mode. Bare minimum this add-on makes that manual approach easier by allowing "Empty"-esque bones to be created in Pose Mode.

## Flaws

Copying bones into a new space hides the source bone until the changes are cancelled or applied. Unfortunately the source bone visibility property is not eligible for undo when working with proxy/library bones however, so Ctrl+Z undo will not restore the visibiliy in that case. Using the "Delete Bone" operator to undo is a workaround, or creating a local copy of the proxy/library armature with the "Make Local Armature" operator.
