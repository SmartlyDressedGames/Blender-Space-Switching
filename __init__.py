"""
This add-on is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This add-on is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this add-on. If not, see <https://www.gnu.org/licenses/>.

Nelson Sexton nelson@smartlydressedgames.com
"""

# Add-ons must contain a bl_info dictionary which Blender uses to read metadata.
# Displayed by the User Preferences add-on listing.
bl_info = {
	"name": "Space Switching",
	"description": "Build temporary armatures while animating.",
	"author": "Nelson Sexton",
	"version": (1, 0, 1),
	"blender": (3, 0, 1),
	"location": "View3D > Pose Mode > Pose > Space Switching",
	"category": "Animation",
}

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator, Menu
import mathutils

def custom_bake(context, frames, pose_bones, do_location = False, do_rotation = False, do_scale = False):
	"""
	Mostly copied from Blender built-in bpy_extras/anim_utils.py in order to support baking individual channels.
	"""
	if not do_location and not do_rotation and not do_scale:
		# Ideally should not be an exception, but this is an easy way to communicate the problem for now.
		raise ValueError("No channels enabled")

	scene = context.scene
	original_frame = scene.frame_current

	# 2D array mapping (bone_index, frame_index) to visual transform.
	pose_info = []
	for _ in range(len(pose_bones)):
		pose_info.append([])

	# Evaluate visual transform for each bone on each frame.
	for frame in frames:
		scene.frame_set(frame)
		context.view_layer.update()

		for pose_bone_index, pose_bone in enumerate(pose_bones):
			matrix = pose_bone.id_data.convert_space(pose_bone = pose_bone,
				matrix = pose_bone.matrix,
				from_space = 'POSE',
				to_space = 'LOCAL')
			pose_info[pose_bone_index].append(matrix)

	# Keyframes could not be inserted during evaluation without affecting the animation of subsequent frames.
	# Now we can insert the keyframes according to evaluated visual transform.
	for pose_bone_index, pose_bone in enumerate(pose_bones):
		euler_prev = None
		quat_prev = None
		for frame_index, frame in enumerate(frames):
			pose_bone.matrix_basis = pose_info[pose_bone_index][frame_index].copy()

			# If connected, location cannot be keyframed because head is constrained to parent's tail.
			if do_location and not pose_bone.bone.use_connect:
				pose_bone.keyframe_insert("location", index=-1, frame=frame, group=pose_bone.name)

			if do_rotation:
				rotation_mode = pose_bone.rotation_mode
				if rotation_mode == 'QUATERNION':
					if quat_prev is not None:
						quat = pose_bone.rotation_quaternion.copy()
						quat.make_compatible(quat_prev)
						pose_bone.rotation_quaternion = quat
						quat_prev = quat
						del quat
					else:
						quat_prev = pose_bone.rotation_quaternion.copy()
					pose_bone.keyframe_insert("rotation_quaternion", index=-1, frame=frame, group=pose_bone.name)
				elif rotation_mode == 'AXIS_ANGLE':
					pose_bone.keyframe_insert("rotation_axis_angle", index=-1, frame=frame, group=pose_bone.name)
				else:  # euler, XYZ, ZXY etc
					if euler_prev is not None:
						euler = pose_bone.rotation_euler.copy()
						euler.make_compatible(euler_prev)
						pose_bone.rotation_euler = euler
						euler_prev = euler
						del euler
					else:
						euler_prev = pose_bone.rotation_euler.copy()
					pose_bone.keyframe_insert("rotation_euler", index=-1, frame=frame, group=pose_bone.name)

			if do_scale:
				pose_bone.keyframe_insert("scale", index=-1, frame=frame, group=pose_bone.name)

	scene.frame_set(original_frame)

def get_space_switching_armature_object(context):
	"""
	Get temporary armature object, or create if it does not exist yet.
	Temporary bones are added to and removed from the temporary armature
	rather than modifying linked/proxy armatures.
	"""
	addon_prefs = context.preferences.addons[__name__].preferences
	existing_object = bpy.data.objects.get(addon_prefs.object_name)
	if existing_object:
		return existing_object
	else:
		new_armature = bpy.data.armatures.new(addon_prefs.armature_name)
		new_obj = bpy.data.objects.new(addon_prefs.object_name, new_armature)
		bpy.context.collection.objects.link(new_obj)
		new_obj.show_in_front = True
		return new_obj

def get_armature_objects():
	"""
	Gather list of objects with armature data.
	Multiple objects can reference the same armature, so bpy.data.armatures is insufficient to get the objects list.
	"""
	result = []
	for obj in bpy.data.objects:
		if obj.type == 'ARMATURE':
			result.append(obj)
	return result

def remove_bone_curves(pose_bone):
	"""
	Blender does not delete animation when a bone is removed, so this function is useful for cleanup.
	"""
	armature = pose_bone.id_data
	if armature.animation_data:
		bone_data_path = pose_bone.path_from_id()
		fcurves = armature.animation_data.action.fcurves
		for fcurve in fcurves:
			if fcurve.data_path.startswith(bone_data_path):
				fcurves.remove(fcurve)

def is_constrained_to(constraint, target_pose_bone):
	if hasattr(constraint, "target") and hasattr(constraint, "subtarget"):
		if constraint.target == target_pose_bone.id_data and constraint.subtarget == target_pose_bone.name:
			return True
	if constraint.type == 'IK' and constraint.pole_target == target_pose_bone.id_data and constraint.pole_subtarget == target_pose_bone.name:
		return True
	return False

def _remove_bones_common(context, do_apply = False, frames = None):
	"""
	Remove selected temporary bones, optionally baking constrained bones.
	"""
	original_objects = context.objects_in_mode
	original_active_pose_bone = context.active_pose_bone

	armature_objects = get_armature_objects()
	if do_apply:
		pose_bones_to_bake = []
		for temp_pose_bone in context.selected_pose_bones:
			# Find pose bones constrained to temp bone.
			for obj in armature_objects:
				for src_pose_bone in obj.pose.bones:
					was_constrained = False
					for constraint in src_pose_bone.constraints:
						if is_constrained_to(constraint, temp_pose_bone):
							was_constrained = True
							break # todo re-use between bake and remove

					if was_constrained:
						pose_bones_to_bake.append(src_pose_bone)

		if len(pose_bones_to_bake) > 0:
			custom_bake(context, frames, pose_bones_to_bake, do_location = True, do_rotation = True, do_scale = True)

	names_of_bones_to_remove = []
	src_bones_to_select = []
	src_bone_to_activate = None

	for temp_pose_bone in context.selected_pose_bones:
		names_of_bones_to_remove.append(temp_pose_bone.name)
		if temp_pose_bone.parent:
			names_of_bones_to_remove.append(temp_pose_bone.parent.name)
			if temp_pose_bone.parent.parent:
				# Remove connected parent bone as well.
				names_of_bones_to_remove.append(temp_pose_bone.parent.parent.name)

		# Cleanup because Blender does not automatically remove animation when bone is removed.
		remove_bone_curves(temp_pose_bone)

		# Find pose bones constrained to temp bone.
		for obj in armature_objects:
			for src_pose_bone in obj.pose.bones:
				was_constrained = False
				for constraint in src_pose_bone.constraints:
					if is_constrained_to(constraint, temp_pose_bone):
						src_pose_bone.constraints.remove(constraint)
						was_constrained = True

				if was_constrained:
					src_pose_bone.bone.hide = False # Was hidden when temp bone was created.
					src_bones_to_select.append((obj, src_pose_bone.name))

					if temp_pose_bone == original_active_pose_bone:
						src_bone_to_activate = (obj, src_pose_bone.name)

	bpy.ops.object.mode_set(mode = 'OBJECT')

	# Clear selection because if any linked/proxy armatures are selected we cannot enter edit mode.
	for obj in original_objects:
		obj.select_set(False)

	temp_obj = get_space_switching_armature_object(context)
	temp_obj.select_set(True)

	# Despite deselecting above, if linked/proxy armature is active we cannot enter edit mode.
	context.view_layer.objects.active = temp_obj

	bpy.ops.object.mode_set(mode = 'EDIT')

	for name in names_of_bones_to_remove:
		temp_edit_bone = temp_obj.data.edit_bones[name]
		temp_obj.data.edit_bones.remove(temp_edit_bone)

	bpy.ops.object.mode_set(mode = 'OBJECT')

	# Reselect objects to ensure their pose bones are selectable.
	for obj in original_objects:
		obj.select_set(True)
		obj.data.bones.active = None

	bpy.ops.object.mode_set(mode = 'POSE')

	# Select the corresponding source bones, if any.
	for armature_obj, bone_name in src_bones_to_select:
		armature_obj.data.bones[bone_name].select = True

	# Mark corresponding source bone active, if applicable.
	if src_bone_to_activate:
		armature_obj, bone_name = src_bone_to_activate
		active_bone = armature_obj.data.bones[bone_name]
		context.view_layer.objects.active = armature_obj
		armature_obj.data.bones.active = active_bone

def _space_switch(context, src_pose_bones, dest_pose_bone, frames = None):
	"""
	Create temporary copies of source bones, bake their animation, and constrain source to copies.
	:param dest_pose_bone: parent space of copies, otherwise world space if None.
	"""
	original_objects = context.objects_in_mode
	src_active_pose_bone = context.active_pose_bone

	# Prevent constraining target space to itself.
	try:
		src_pose_bones.remove(dest_pose_bone)
	except:
		pass # EAFP

	if len(src_pose_bones) < 1:
		# Was probably trying to constrain single bone to itself, removed above.
		# todo is there a good way to communicate this to the user?
		return

	# Clear original bone selection because temporary bones will be selected instead.
	for pose_bone in context.selected_pose_bones:
		pose_bone.bone.select = False

	for pose_bone in src_pose_bones:
		# Prevent selecting in viewport until copy bone is removed.
		# Does not work properly with undo on library/proxy armatures because it is a Bone property rather than PoseBone. As a workaround
		# the make_local_armature operator can be used to allow edit access on linked armatures.
		pose_bone.bone.hide = True

	bpy.ops.object.mode_set(mode = 'OBJECT')

	# Clear selection because if any linked/proxy armatures are selected we cannot enter edit mode.
	for obj in original_objects:
		obj.select_set(False)

	temp_obj = get_space_switching_armature_object(context)
	temp_obj.select_set(True)

	# Despite deselecting above, if linked/proxy armature is active we cannot enter edit mode.
	context.view_layer.objects.active = temp_obj

	bpy.ops.object.mode_set(mode = 'EDIT')

	addon_prefs = context.preferences.addons[__name__].preferences

	temp_bone_names = [] # Use names because edit bones cannot be referenced after leaving edit mode.
	for src_pose_bone in src_pose_bones:
		desired_name = addon_prefs.copy_name.format(
			bone_name = src_pose_bone.name,
			armature_name = src_pose_bone.bone.id_data.name,
			object_name = src_pose_bone.id_data.name,
			)
		temp_edit_bone = temp_obj.data.edit_bones.new(desired_name)
		temp_edit_bone.head = (0.0, 0.0, 0.0)
		temp_edit_bone.tail = (0.0, src_pose_bone.bone.length, 0.0)
		temp_edit_bone.use_deform = False # Helpful to prevent accidentally exporting temporary bones.
		temp_bone_names.append(temp_edit_bone.name)

		# If connected, source head location is constrained to parent's tail location.
		if src_pose_bone.bone.use_connect:
			# We could use a location constraint to similarly constrain the copy. Instead we always create a parent bone, even for world
			# space, with its tail constrained to the source parent's tail. This allows the copy to also be connected which has several pros:
			# 1. Baking does not keyframe the location because it knows the copy is connected.
			# 2. Location cannot accidentally be modified by mistake.
			# 3. Constraint cannot accidentally be removed by mistake.
			# 4. Other constraints can be added by the user without concern.
			# Con: two parent bones are necessary because parent must pivot around its tail.
			parent_pose_bone = src_pose_bone.parent
			parent_desired_name = addon_prefs.parent_name.format(
				bone_name = src_pose_bone.name,
				armature_name = src_pose_bone.bone.id_data.name,
				object_name = src_pose_bone.id_data.name,
				)
			if dest_pose_bone:
				child_desired_name = addon_prefs.space_name.format(
					bone_name = dest_pose_bone.name,
					armature_name = dest_pose_bone.bone.id_data.name,
					object_name = dest_pose_bone.id_data.name,
					)
			else:
				child_desired_name = addon_prefs.space_name.format(
					bone_name = parent_pose_bone.name,
					armature_name = parent_pose_bone.bone.id_data.name,
					object_name = parent_pose_bone.id_data.name,
					)
			temp_parent_edit_bone = temp_obj.data.edit_bones.new(parent_desired_name)
			temp_parent_edit_bone.head = (0.0, 0.0, 0.0)
			temp_parent_edit_bone.tail = (0.0, 1.0, 0.0)
			temp_parent_edit_bone.use_deform = False # Helpful to prevent accidentally exporting temporary bones.
			temp_child_edit_bone = temp_obj.data.edit_bones.new(child_desired_name)
			temp_child_edit_bone.head = (0.0, -1.0, 0.0)
			temp_child_edit_bone.tail = (0.0, 0.0, 0.0)
			temp_child_edit_bone.use_deform = False # Helpful to prevent accidentally exporting temporary bones.
			temp_child_edit_bone.parent = temp_parent_edit_bone
			temp_edit_bone.parent = temp_child_edit_bone
			temp_edit_bone.use_connect = True
		elif dest_pose_bone:
			desired_name = addon_prefs.space_name.format(
				bone_name = dest_pose_bone.name,
				armature_name = dest_pose_bone.bone.id_data.name,
				object_name = dest_pose_bone.id_data.name,
				)
			temp_dest_edit_bone = temp_obj.data.edit_bones.new(desired_name)
			temp_dest_edit_bone.head = (0.0, 0.0, 0.0)
			temp_dest_edit_bone.tail = (0.0, dest_pose_bone.bone.length, 0.0)
			temp_dest_edit_bone.use_deform = False # Helpful to prevent accidentally exporting temporary bones.
			temp_edit_bone.parent = temp_dest_edit_bone

	bpy.ops.object.mode_set(mode = 'OBJECT')

	# Reselect objects to ensure their pose bones are selectable.
	for obj in original_objects:
		obj.select_set(True)
		obj.data.bones.active = None

	bpy.ops.object.mode_set(mode = 'POSE')

	temp_pose_bones = []
	constraints_to_remove = []

	if len(src_pose_bones) != len(temp_bone_names):
		raise RuntimeError("edit bones list mismatch")

	for src_pose_bone, temp_bone_name in zip(src_pose_bones, temp_bone_names):
		temp_bone = temp_obj.data.bones[temp_bone_name]
		temp_bone.select = True
		temp_bone.space_switching_tag = 'COPY'
		temp_pose_bone = temp_obj.pose.bones[temp_bone_name]
		temp_pose_bones.append(temp_pose_bone)

		src_rotation_mode = src_pose_bone.rotation_mode
		if src_pose_bone.bone.use_connect and src_rotation_mode != 'QUATERNION' and src_rotation_mode != 'AXIS_ANGLE':
			# Euler mode will probably not work well in target space, so change rotation mode to quaternion.
			temp_pose_bone.rotation_mode = 'QUATERNION'
		else:
			# Copy rotation mode (e.g. XYZ Euler) - particularly important for graph editor.
			temp_pose_bone.rotation_mode = src_pose_bone.rotation_mode
			temp_pose_bone.rotation_axis_angle = src_pose_bone.rotation_axis_angle

		# Temporary bone copies viewport display properties because the source bone is hidden.
		temp_pose_bone.custom_shape = src_pose_bone.custom_shape
		temp_pose_bone.custom_shape_translation = src_pose_bone.custom_shape_translation
		temp_pose_bone.custom_shape_rotation_euler = src_pose_bone.custom_shape_rotation_euler
		temp_pose_bone.custom_shape_scale_xyz = src_pose_bone.custom_shape_scale_xyz
		temp_pose_bone.custom_shape_transform = src_pose_bone.custom_shape_transform
		temp_pose_bone.use_custom_shape_bone_size = src_pose_bone.use_custom_shape_bone_size # "Scale to Bone Length"
		temp_bone.show_wire = src_pose_bone.bone.show_wire # "Wireframe"

		# We could copy whether each channel is locked, but the locks do not necessarily make sense in the target space.
		# temp_pose_bone.lock_location = src_pose_bone.lock_location
		# temp_pose_bone.lock_rotation = src_pose_bone.lock_rotation
		# temp_pose_bone.lock_rotation_w = src_pose_bone.lock_rotation_w
		# temp_pose_bone.lock_rotations_4d = src_pose_bone.lock_rotations_4d
		# temp_pose_bone.lock_scale = src_pose_bone.lock_scale

		# Constrain temporary bone to source bone to copy source animation.
		copy_transforms = temp_pose_bone.constraints.new('COPY_TRANSFORMS')
		copy_transforms.target = src_pose_bone.id_data
		copy_transforms.subtarget = src_pose_bone.name
		constraints_to_remove.append(copy_transforms)

		if src_pose_bone == src_active_pose_bone:
			# temp_obj is already active from before entering edit mode.
			temp_obj.data.bones.active = temp_bone

		if temp_pose_bone.parent:
			temp_dest_pose_bone = temp_pose_bone.parent
			temp_dest_bone = temp_dest_pose_bone.bone
			if src_pose_bone.bone.use_connect:
				temp_dest_bone.hide = True
				temp_dest_bone.space_switching_tag = 'SPACE'
				temp_dest_pose_bone = temp_dest_pose_bone.parent
				temp_dest_bone = temp_dest_pose_bone.bone
			temp_dest_bone.hide = True
			temp_dest_bone.space_switching_tag = 'SPACE'

			copy_location = temp_dest_pose_bone.constraints.new('COPY_LOCATION')
			# If connected, source head location is constrained to source parent's tail location.
			if src_pose_bone.bone.use_connect:
				# Constrain copy parent's tail location to source parent's tail location. This allows the copy to also be connected.
				copy_location.target = src_pose_bone.parent.id_data
				copy_location.subtarget = src_pose_bone.parent.name
				copy_location.head_tail = 1.0
			elif dest_pose_bone:
				copy_location.target = dest_pose_bone.id_data
				copy_location.subtarget = dest_pose_bone.name

			if dest_pose_bone:
				# Do not copy rotation mode to avoid gimbal lock that may happen without the full parent hierarchy.
				copy_rotation = temp_dest_pose_bone.constraints.new('COPY_ROTATION')
				copy_rotation.target = dest_pose_bone.id_data
				copy_rotation.subtarget = dest_pose_bone.name

	custom_bake(context, frames, temp_pose_bones, do_location = True, do_rotation = True, do_scale = True)

	if len(src_pose_bones) != len(temp_pose_bones) or len(src_pose_bones) != len(constraints_to_remove):
		raise RuntimeError("reverse constraints list mismatch")

	for src_pose_bone, temp_pose_bone, constraint in zip(src_pose_bones, temp_pose_bones, constraints_to_remove):
		temp_pose_bone.constraints.remove(constraint)

		# If connected, head location is already constrained to parent's tail location.
		if not src_pose_bone.bone.use_connect:
			reverse_copy_location = src_pose_bone.constraints.new('COPY_LOCATION')
			reverse_copy_location.target = temp_pose_bone.id_data
			reverse_copy_location.subtarget = temp_pose_bone.name

		reverse_copy_rotation = src_pose_bone.constraints.new('COPY_ROTATION')
		reverse_copy_rotation.target = temp_pose_bone.id_data
		reverse_copy_rotation.subtarget = temp_pose_bone.name

class SPACE_SWITCHING_AddonPreferences(bpy.types.AddonPreferences):
	"""
	Properties panel under the User Preferences add-on listing.
	"""
	bl_idname = __name__ # Must match the name of the add-on module.

	object_name: StringProperty(
		name = "Object Name",
		description = "Custom naming conventions for object with temporary bones",
		default = "SpaceSwitching"
		)

	armature_name: StringProperty(
		name = "Armature Name",
		description = "Custom naming conventions for armature with temporary bones",
		default = "SpaceSwitchingArmature"
		)

	empty_name: StringProperty(
		name = "Empty Name",
		description = "Custom naming conventions for unconstrained temporary bone",
		default = "Empty"
		)

	copy_name: StringProperty(
		name = "Copy Name",
		description = """Custom naming conventions for constrained temporary bone.
Available formatting arguments:
{bone_name}: name of selected source bone.
{armature_name}: name of selected source bone's armature.
{object_name}: name of selected source bone's object""",
		default = "{bone_name}_Copy"
		)

	parent_name: StringProperty(
		name = "Parent Name",
		description = """Custom naming conventions for parent space temporary bone.
Available formatting arguments:
{bone_name}: name of selected source bone's parent.
{armature_name}: name of selected source bone's parent's armature.
{object_name}: name of selected source bone's parent's object""",
		default = "{bone_name}_Parent"
		)

	space_name: StringProperty(
		name = "Space Name",
		description = """Custom naming conventions for target space temporary bone.
Available formatting arguments:
{bone_name}: name of selected source bone.
{armature_name}: name of selected source bone's armature.
{object_name}: name of selected source bone's object""",
		default = "{bone_name}_Space"
		)

	local_armature_object_name: StringProperty(
		name = "Local Armature Object Name",
		description = """Custom naming conventions for duplicate object.
Available formatting arguments:
{object}: name of selected source object.
{armature}: name of selected source object's armature""",
		default = "{object}_Local"
		)

	def draw(self, _context):
		layout = self.layout
		layout.prop(self, "object_name")
		layout.prop(self, "armature_name")
		layout.prop(self, "empty_name")
		layout.prop(self, "copy_name")
		layout.prop(self, "parent_name")
		layout.prop(self, "space_name")
		layout.prop(self, "local_armature_object_name")

class SPACE_SWITCHING_OT_bake_pose(Operator):
	bl_description = "Specialized/optimized for pose bones"
	bl_idname = "space_switching.bake_pose"
	bl_label = "Bake Pose"
	bl_options = {'REGISTER', 'UNDO'}

	frame_start: IntProperty(
		name = "Start Frame",
		description = "Start frame for baking",
		min=0, max=300000,
		default=1,
		)

	frame_end: IntProperty(
		name = "End Frame",
		description = "End frame for baking",
		min=1, max=300000,
		default=250,
		)

	do_location: BoolProperty(name = "Location", default = True)
	do_rotation: BoolProperty(name = "Rotation", default = True)
	do_scale: BoolProperty(name = "Scale", default = False)

	@classmethod
	def poll(cls, context):
		"""
		Must have bone(s) selected in pose mode, and an action to bake into.
		"""
		return (context.mode == 'POSE'
			and len(context.selected_pose_bones) > 0
			and context.active_object.animation_data is not None
			and context.active_object.animation_data.action is not None)

	def execute(self, context):
		custom_bake(context,
			range(self.frame_start, self.frame_end + 1),
			context.selected_pose_bones,
			do_location = self.do_location,
			do_rotation = self.do_rotation,
			do_scale = self.do_scale)
		return {'FINISHED'}

	def invoke(self, context, _event):
		scene = context.scene
		self.frame_start = scene.frame_start
		self.frame_end = scene.frame_end
		return context.window_manager.invoke_props_dialog(self)

class SPACE_SWITCHING_OT_add_empty(Operator):
	bl_description = """Add bone at location of the 3D cursor. For complex manual setups this is easier to use than a regular empty object
because it can be selected without repeatedly changing between Pose Mode and Object Mode"""
	bl_idname = "space_switching.add_empty"
	bl_label = "Add Empty"
	bl_options = {'REGISTER', 'UNDO'}

	length: FloatProperty(
		name = "Length",
		description = "Distance from head to tail of bone",
		min=0.0,
		default=1.0,
		)

	@classmethod
	def poll(cls, context):
		return context.mode == 'POSE'

	def execute(self, context):
		original_objects = context.objects_in_mode
		for pose_bone in context.selected_pose_bones:
			pose_bone.bone.select = False

		bpy.ops.object.mode_set(mode = 'OBJECT')

		# Clear selection because if any linked/proxy armatures are selected we cannot enter edit mode.
		for obj in original_objects:
			obj.select_set(False)

		temp_obj = get_space_switching_armature_object(context)
		temp_obj.select_set(True)

		# Despite deselecting above, if linked/proxy armature is active we cannot enter edit mode.
		context.view_layer.objects.active = temp_obj

		bpy.ops.object.mode_set(mode = 'EDIT')

		addon_prefs = context.preferences.addons[__name__].preferences
		temp_edit_bone = temp_obj.data.edit_bones.new(addon_prefs.empty_name)
		temp_edit_bone.head = context.scene.cursor.location
		temp_edit_bone.tail = context.scene.cursor.location + mathutils.Vector((0.0, self.length, 0.0))
		temp_edit_bone.use_deform = False # Helpful to prevent accidentally exporting temporary bones.
		temp_pose_bone_name = temp_edit_bone.name

		bpy.ops.object.mode_set(mode = 'OBJECT')

		# Reselect objects to ensure their pose bones are selectable.
		for obj in original_objects:
			obj.select_set(True)
			obj.data.bones.active = None

		bpy.ops.object.mode_set(mode = 'POSE')

		temp_bone = temp_obj.data.bones[temp_pose_bone_name]
		temp_bone.select = True
		temp_bone.space_switching_tag = 'EMPTY'
		temp_obj.data.bones.active = temp_bone

		return {'FINISHED'}

	def invoke(self, context, _event):
		return context.window_manager.invoke_props_dialog(self)

class SPACE_SWITCHING_OT_delete_bone(Operator):
	bl_description = "Delete bone(s) created by space switching add-on without baking constrained bones"
	bl_idname = "space_switching.delete_bone"
	bl_label = "Delete Bone"
	bl_options = {'REGISTER', 'UNDO'}

	@classmethod
	def poll(cls, context):
		"""
		Must have only temporary bone(s) selected in pose mode.
		"""
		if context.mode == 'POSE':
			for pose_bone in context.selected_pose_bones:
				if pose_bone.bone.space_switching_tag == 'NONE':
					# Operator is only available when all of the selected bones will be deleted.
					return False
			return len(context.selected_pose_bones) > 0
		return False

	def execute(self, context):
		_remove_bones_common(context)
		return {'FINISHED'}

class SPACE_SWITCHING_OT_apply_bone(Operator):
	bl_description = "Remove bone(s) created by space switching add-on after baking constrained bones"
	bl_idname = "space_switching.apply_bone"
	bl_label = "Apply Bone"
	bl_options = {'REGISTER', 'UNDO'}

	frame_start: IntProperty(
		name = "Start Frame",
		description = "Start frame for baking",
		min=0, max=300000,
		default=1,
		)

	frame_end: IntProperty(
		name = "End Frame",
		description = "End frame for baking",
		min=1, max=300000,
		default=250,
		)

	@classmethod
	def poll(cls, context):
		"""
		Must have only bakeable bone(s) selected in pose mode.
		"""
		if context.mode == 'POSE':
			for pose_bone in context.selected_pose_bones:
				if pose_bone.bone.space_switching_tag != 'COPY':
					# Operator is only available when all of the selected bones will be baked.
					return False
			return len(context.selected_pose_bones) > 0
		return False

	def execute(self, context):
		_remove_bones_common(context, do_apply = True, frames = range(self.frame_start, self.frame_end + 1))
		return {'FINISHED'}

	def invoke(self, context, _event):
		scene = context.scene
		self.frame_start = scene.frame_start
		self.frame_end = scene.frame_end
		return context.window_manager.invoke_props_dialog(self)

class SPACE_SWITCHING_OT_selection_to_world(Operator):
	bl_description = "Switch selected bones to world space"
	bl_idname = "space_switching.selection_to_world"
	bl_label = "Switch to World"
	bl_options = {'REGISTER', 'UNDO'}

	frame_start: IntProperty(
		name = "Start Frame",
		description = "Start frame for baking",
		min=0, max=300000,
		default=1,
		)

	frame_end: IntProperty(
		name = "End Frame",
		description = "End frame for baking",
		min=1, max=300000,
		default=250,
		)

	@classmethod
	def poll(cls, context):
		"""
		Must have only unconstrained bone(s) selected in pose mode.
		"""
		if context.mode == 'POSE':
			for pose_bone in context.selected_pose_bones:
				if len(pose_bone.constraints) > 0:
					# Already constrained so do not add another constraint.
					return False
			return len(context.selected_pose_bones) > 0
		return False

	def execute(self, context):
		_space_switch(context, context.selected_pose_bones, None, frames = range(self.frame_start, self.frame_end + 1))
		return {'FINISHED'}

	def invoke(self, context, _event):
		scene = context.scene
		self.frame_start = scene.frame_start
		self.frame_end = scene.frame_end
		return context.window_manager.invoke_props_dialog(self)

class SPACE_SWITCHING_OT_selection_to_active(Operator):
	bl_description = "Switch selected bones to active bone space"
	bl_idname = "space_switching.selection_to_active"
	bl_label = "Switch to Active"
	bl_options = {'REGISTER', 'UNDO'}

	frame_start: IntProperty(
		name = "Start Frame",
		description = "Start frame for baking",
		min=0, max=300000,
		default=1,
		)

	frame_end: IntProperty(
		name = "End Frame",
		description = "End frame for baking",
		min=1, max=300000,
		default=250,
		)

	@classmethod
	def poll(cls, context):
		"""
		Must have only unconstrained bone(s) selected in pose mode, and more than one
		because active is excluded from bones to switch.
		"""
		if context.mode == 'POSE':
			for pose_bone in context.selected_pose_bones:
				if len(pose_bone.constraints) > 0:
					# Already constrained so do not add another constraint.
					return False
			return len(context.selected_pose_bones) > 1
		return False

	def execute(self, context):
		src_pose_bones = context.selected_pose_bones
		dest_pose_bone = context.active_pose_bone
		_space_switch(context, src_pose_bones, dest_pose_bone, frames = range(self.frame_start, self.frame_end + 1))
		return {'FINISHED'}

	def invoke(self, context, _event):
		scene = context.scene
		self.frame_start = scene.frame_start
		self.frame_end = scene.frame_end
		return context.window_manager.invoke_props_dialog(self)

class SPACE_SWITCHING_OT_selection_to_target(Operator):
	bl_description = "Switch selected bones to target bone space"
	bl_idname = "space_switching.selection_to_target"
	bl_label = "Switch to Target"
	bl_options = {'REGISTER', 'UNDO'}

	# Property naming matches constraint naming.
	target: StringProperty(name = "Target") # PointerProperty is apparently incompatible with operators?
	subtarget: StringProperty(name = "Bone")

	frame_start: IntProperty(
		name = "Start Frame",
		description = "Start frame for baking",
		min=0, max=300000,
		default=1,
		)

	frame_end: IntProperty(
		name = "End Frame",
		description = "End frame for baking",
		min=1, max=300000,
		default=250,
		)

	@classmethod
	def poll(cls, context):
		"""
		Must have only unconstrained bone(s) selected in pose mode.
		"""
		if context.mode == 'POSE':
			for pose_bone in context.selected_pose_bones:
				if len(pose_bone.constraints) > 0:
					# Already constrained so do not add another constraint.
					return False
			return len(context.selected_pose_bones) > 0
		return False

	def execute(self, context):
		if not self.target:
			self.report({'ERROR'}, "Cannot switch because Target was not set.")
			return {'FINISHED'}

		if not self.subtarget:
			self.report({'ERROR'}, "Cannot switch because Bone was not set.")
			return {'FINISHED'}

		target = context.scene.objects.get(self.target)
		if not target or target.type != 'ARMATURE':
			self.report({'ERROR'}, f"Cannot switch because Target {target} is not an armature.")
			return {'FINISHED'}

		src_pose_bones = context.selected_pose_bones
		dest_pose_bone = target.pose.bones[self.subtarget]
		_space_switch(context, src_pose_bones, dest_pose_bone, frames = range(self.frame_start, self.frame_end + 1))
		return {'FINISHED'}

	def invoke(self, context, _event):
		"""
		Props dialog lets user select target space.
		"""
		self.target = context.active_object.name
		self.subtarget = context.active_pose_bone.name
		# todo is there any way to prevent executing when dialog properties are invalid?
		return context.window_manager.invoke_props_dialog(self)

	def draw(self, context):
		layout = self.layout
		col = layout.column()
		col.prop_search(self, "target", context.scene, "objects")
		if not self.target:
			return

		target = context.scene.objects.get(self.target)
		if not target or target.type != 'ARMATURE':
			return

		col.prop_search(self, "subtarget", target.pose, "bones")
		if not self.subtarget:
			return

		col.prop(self, "frame_start")
		col.prop(self, "frame_end")

class SPACE_SWITCHING_OT_build_two_bone_ik(Operator):
	bl_description = "(WIP) Bake two-bone IK in world space"
	bl_idname = "space_switching.build_two_bone_ik"
	bl_label = "Build Two-Bone IK (WIP)"
	bl_options = {'REGISTER', 'UNDO'}

	length: FloatProperty(
		name = "Length",
		description = "Distance from head to tail of bone",
		min=0.0,
		default=1.0,
		)

	# todo calculate pole angle?
	pole_angle: FloatProperty(
		name = "Pole Angle",
		description = "Pole rotation offset",
		subtype = 'ANGLE',
		)

	frame_start: IntProperty(
		name = "Start Frame",
		description = "Start frame for baking",
		min=0, max=300000,
		default=1,
		)

	frame_end: IntProperty(
		name = "End Frame",
		description = "End frame for baking",
		min=1, max=300000,
		default=250,
		)

	@classmethod
	def poll(cls, context):
		"""
		Must have only unconstrained bone(s) selected in pose mode.
		"""
		if context.mode == 'POSE':
			for pose_bone in context.selected_pose_bones:
				if len(pose_bone.constraints) > 0:
					# Already constrained so do not add another constraint.
					return False
			return len(context.selected_pose_bones) == 1
		return False

	def execute(self, context):
		original_objects = context.objects_in_mode
		src_pose_bone = context.active_pose_bone

		for pose_bone in context.selected_pose_bones:
			pose_bone.bone.select = False

		bpy.ops.object.mode_set(mode = 'OBJECT')

		# Clear selection because if any linked/proxy armatures are selected we cannot enter edit mode.
		for obj in original_objects:
			obj.select_set(False)

		temp_obj = get_space_switching_armature_object(context)
		temp_obj.select_set(True)

		# Despite deselecting above, if linked/proxy armature is active we cannot enter edit mode.
		context.view_layer.objects.active = temp_obj

		bpy.ops.object.mode_set(mode = 'EDIT')

		target_edit_bone = temp_obj.data.edit_bones.new("ik_target")
		target_edit_bone.head = (0.0, 0.0, 0.0)
		target_edit_bone.tail = (0.0, self.length, 0.0)
		target_edit_bone.use_deform = False # Helpful to prevent accidentally exporting temporary bones.
		target_edit_bone_name = target_edit_bone.name

		pole_target_edit_bone = temp_obj.data.edit_bones.new("ik_pole_target")
		pole_target_edit_bone.head = (0.0, 0.0, 0.0)
		pole_target_edit_bone.tail = (0.0, self.length, 0.0)
		pole_target_edit_bone.use_deform = False # Helpful to prevent accidentally exporting temporary bones.
		pole_target_edit_bone_name = pole_target_edit_bone.name

		bpy.ops.object.mode_set(mode = 'OBJECT')

		# Reselect objects to ensure their pose bones are selectable.
		for obj in original_objects:
			obj.select_set(True)
			obj.data.bones.active = None

		bpy.ops.object.mode_set(mode = 'POSE')

		target_bone = temp_obj.data.bones[target_edit_bone_name]
		target_bone.select = True
		target_bone.space_switching_tag = 'EMPTY'
		pole_target_bone = temp_obj.data.bones[pole_target_edit_bone_name]
		pole_target_bone.select = True
		pole_target_bone.space_switching_tag = 'EMPTY'

		target_pose_bone = temp_obj.pose.bones[target_edit_bone_name]
		target_copy_location = target_pose_bone.constraints.new('COPY_LOCATION')
		target_copy_location.target = src_pose_bone.id_data
		target_copy_location.subtarget = src_pose_bone.name
		target_copy_location.head_tail = 1.0

		pole_target_pose_bone = temp_obj.pose.bones[pole_target_edit_bone_name]
		pole_target_copy_location = pole_target_pose_bone.constraints.new('COPY_LOCATION')
		pole_target_copy_location.target = src_pose_bone.id_data
		pole_target_copy_location.subtarget = src_pose_bone.name

		frames = range(self.frame_start, self.frame_end + 1)
		custom_bake(context, frames, [ target_pose_bone, pole_target_pose_bone ], do_location = True)

		target_pose_bone.constraints.remove(target_copy_location)
		pole_target_pose_bone.constraints.remove(pole_target_copy_location)

		ik_constraint = src_pose_bone.constraints.new('IK')
		ik_constraint.target = temp_obj
		ik_constraint.subtarget = target_edit_bone_name
		ik_constraint.pole_target = temp_obj
		ik_constraint.pole_subtarget = pole_target_edit_bone_name
		ik_constraint.chain_count = 2
		ik_constraint.pole_angle = self.pole_angle

		return {'FINISHED'}

	def invoke(self, context, _event):
		scene = context.scene
		self.frame_start = scene.frame_start
		self.frame_end = scene.frame_end
		return context.window_manager.invoke_props_dialog(self)

class SPACE_SWITCHING_OT_make_local_armature(Operator):
	bl_description = """Constrain proxy armature to local duplicate. Combines the benefits of full edit access with linking armature.
Mostly a workaround to fully support undo because library/proxy undo only works with PoseBone properties. (Space switching operators
modify Bone.hide_select) If linked file is updated this operator can be repeated without animation loss"""
	bl_idname = "space_switching.make_local_armature"
	bl_label = "Make Local Armature"
	bl_options = {'REGISTER', 'UNDO'}

	frame_start: IntProperty(
		name = "Start Frame",
		description = "Start frame for baking",
		min=0, max=300000,
		default=1,
		)

	frame_end: IntProperty(
		name = "End Frame",
		description = "End frame for baking",
		min=1, max=300000,
		default=250,
		)

	@classmethod
	def poll(cls, context):
		"""
		Must have single proxy armature selected in object mode.
		"""
		return context.mode == 'OBJECT' and context.object and context.object.type == 'ARMATURE'

	def execute(self, context):
		src_object = context.object

		# Un-hide from viewport so duplicate is not hidden. (we might be updating an existing local copy)
		src_object.hide_viewport = False

		# Check whether all pose bones are already constrained to an object, indicating a duplicate already exists.
		old_object = None
		src_constraints_to_remove = []
		for src_pose_bone in src_object.pose.bones:
			if len(src_pose_bone.constraints) > 1:
				self.report({'ERROR'}, f"Unable to determine existing local object because bone {src_pose_bone.name} has more than one constraint.")
				return {'FINISHED'}
			elif len(src_pose_bone.constraints) == 1:
				constraint = src_pose_bone.constraints[0]
				if old_object == None:
					old_object = constraint.target
				elif old_object != constraint.target:
					self.report({'ERROR'}, "Unable to determine existing local object because {src_object.name} is constrained to more than one target.")
					return {'FINISHED'}
				src_constraints_to_remove.append((src_pose_bone, constraint))

		frames = range(self.frame_start, self.frame_end + 1)

		# If duplicate already exists then we need to preserve its animation by baking down to the linked source before deleting.
		if old_object:
			bpy.ops.object.mode_set(mode = 'POSE')
			custom_bake(context, frames, src_object.pose.bones, do_location = True, do_rotation = True, do_scale = True)
			bpy.ops.object.mode_set(mode = 'OBJECT')

			bpy.ops.object.delete({"selected_objects": [old_object]})

		# User might have deleted old_object manually before running the operator.
		for pose_bone, constraint in src_constraints_to_remove:
			pose_bone.constraints.remove(constraint)

		# Ideally we would not use ops but this is easy, todo?
		bpy.ops.object.duplicate({"selected_objects": [src_object]}) # Specify objects, otherwise hidden source is not duplicated.

		# Operator sets the duplicate active.
		dest_object = context.active_object
		if dest_object == src_object:
			self.report({'ERROR'}, "Failed to duplicate source object.")
			return {'FINISHED'}

		bpy.ops.object.make_local(type='SELECT_OBDATA')

		addon_prefs = context.preferences.addons[__name__].preferences
		dest_object.name = addon_prefs.local_armature_object_name.format(
					object = src_object.name,
					armature = src_object.data.name,
					)

		# Constrain local duplicate to proxy source.
		dest_constraints_to_remove = []
		for dest_pose_bone in dest_object.pose.bones:
			copy_transforms = dest_pose_bone.constraints.new('COPY_TRANSFORMS')
			copy_transforms.target = src_object
			copy_transforms.subtarget = dest_pose_bone.name
			dest_constraints_to_remove.append((dest_pose_bone, copy_transforms))

		# Bake proxy source animation down to local duplicate, preserving existing animation from old duplicate.
		context.view_layer.objects.active = dest_object
		bpy.ops.object.mode_set(mode = 'POSE')
		custom_bake(context, frames, dest_object.pose.bones, do_location = True, do_rotation = True, do_scale = True)
		bpy.ops.object.mode_set(mode = 'OBJECT')

		for pose_bone, constraint in dest_constraints_to_remove:
			pose_bone.constraints.remove(constraint)

		# Constrain proxy source to local duplicate now that animation has been copied over.
		for src_pose_bone in src_object.pose.bones:
			copy_transforms = src_pose_bone.constraints.new('COPY_TRANSFORMS')
			copy_transforms.target = dest_object
			copy_transforms.subtarget = src_pose_bone.name

		# Hide from viewport to ensure user does not accidentally select.
		src_object.hide_viewport = True

		return {'FINISHED'}

	def invoke(self, context, _event):
		scene = context.scene
		self.frame_start = scene.frame_start
		self.frame_end = scene.frame_end
		return context.window_manager.invoke_props_dialog(self)

class VIEW3D_MT_space_switching(Menu):
	"""
	Menu available in Pose Mode topbar.
	"""
	bl_label = "Space Switching"
	def draw(self, _context):
		layout = self.layout

		# If bound to key, menus default to EXEC_REGION_WIN which does not allow user to set operator properties.
		layout.operator_context = 'INVOKE_DEFAULT'

		layout.operator(SPACE_SWITCHING_OT_bake_pose.bl_idname)
		layout.operator(SPACE_SWITCHING_OT_add_empty.bl_idname)
		layout.operator(SPACE_SWITCHING_OT_delete_bone.bl_idname)
		layout.operator(SPACE_SWITCHING_OT_apply_bone.bl_idname)
		layout.operator(SPACE_SWITCHING_OT_selection_to_world.bl_idname)
		layout.operator(SPACE_SWITCHING_OT_selection_to_active.bl_idname)
		layout.operator(SPACE_SWITCHING_OT_selection_to_target.bl_idname)
		layout.operator(SPACE_SWITCHING_OT_build_two_bone_ik.bl_idname)

class VIEW3D_MT_space_switching_pie(Menu):
	"""
	Pie menu bound to 'X' key by default.
	"""
	bl_label = "Space Switching"
	def draw(self, _context):
		layout = self.layout
		pie = layout.menu_pie()

		pie.operator(SPACE_SWITCHING_OT_bake_pose.bl_idname)
		pie.operator(SPACE_SWITCHING_OT_add_empty.bl_idname)
		pie.operator(SPACE_SWITCHING_OT_delete_bone.bl_idname)
		pie.operator(SPACE_SWITCHING_OT_apply_bone.bl_idname)
		pie.operator(SPACE_SWITCHING_OT_selection_to_world.bl_idname)
		pie.operator(SPACE_SWITCHING_OT_selection_to_active.bl_idname)
		pie.operator(SPACE_SWITCHING_OT_selection_to_target.bl_idname)

class VIEW3D_MT_object_space_switching(Menu):
	"""
	Menu available in Object Mode topbar.
	"""
	bl_label = "Space Switching"
	def draw(self, _context):
		layout = self.layout

		# If bound to key, menus default to EXEC_REGION_WIN which does not allow user to set operator properties.
		layout.operator_context = 'INVOKE_DEFAULT'

		layout.operator(SPACE_SWITCHING_OT_make_local_armature.bl_idname)

# Classes to register with Blender.
addon_classes = [
	SPACE_SWITCHING_AddonPreferences,
	SPACE_SWITCHING_OT_bake_pose,
	SPACE_SWITCHING_OT_add_empty,
	SPACE_SWITCHING_OT_delete_bone,
	SPACE_SWITCHING_OT_apply_bone,
	SPACE_SWITCHING_OT_selection_to_world,
	SPACE_SWITCHING_OT_selection_to_active,
	SPACE_SWITCHING_OT_selection_to_target,
	SPACE_SWITCHING_OT_build_two_bone_ik,
	SPACE_SWITCHING_OT_make_local_armature,
	VIEW3D_MT_space_switching,
	VIEW3D_MT_space_switching_pie,
	VIEW3D_MT_object_space_switching,
]

def pose_menu_func(self, _context):
	"""
	Callback when building the Pose Mode topbar Pose menu.
	"""
	layout = self.layout
	layout.separator()
	layout.menu(VIEW3D_MT_space_switching.__name__)

def object_menu_func(self, _context):
	"""
	Callback when building the Object Mode topbar Object menu.
	"""
	layout = self.layout
	layout.separator()
	layout.menu(VIEW3D_MT_object_space_switching.__name__)

def register():
	"""
	Called by Blender.
	"""
	tag_items = [ # (identifier, name, description, icon, number)
		('NONE', "None", "Bone was not generated by the space switching addon.", 0, 0),
		('EMPTY', "Empty", "Pose mode equivalent of an Empty object.", 0, 1),
		('SPACE', "Space", "Parent of temporary bones allowing them to be children of a target space.", 0, 2),
		('COPY', "Copy", "Temporary copy of source bone. Source bone is hidden and constrained to the copy.", 0, 3),
	]
	bpy.types.Bone.space_switching_tag = bpy.props.EnumProperty(items = tag_items,
		name = "Space Switching Tag",
		description = "Internal value used by the space switching addon.")

	for cls in addon_classes:
		bpy.utils.register_class(cls)

	bpy.types.VIEW3D_MT_pose.append(pose_menu_func) # Extend the Pose Mode topbar Pose menu.
	bpy.types.VIEW3D_MT_object.append(object_menu_func) # Extend the Object Mode topbar Object menu.

	# Bind 'X' to space switching menu in pose mode.
	# This can be modified in User Preferences under Keymap > 3D View > Pose > Pose (Global)
	key_config = bpy.context.window_manager.keyconfigs.addon
	global addon_keymaps
	addon_keymaps = []
	keymap = key_config.keymaps.new(name = 'Pose', space_type = 'EMPTY')
	keymap_item_menu = keymap.keymap_items.new("wm.call_menu", 'X', 'PRESS', alt = True)
	keymap_item_menu.properties.name = "VIEW3D_MT_space_switching"
	addon_keymaps.append((keymap, keymap_item_menu))
	keymap_item_pie = keymap.keymap_items.new("wm.call_menu_pie", 'X', 'PRESS')
	keymap_item_pie.properties.name = "VIEW3D_MT_space_switching_pie"
	addon_keymaps.append((keymap, keymap_item_pie))

def unregister():
	"""
	Called by Blender. Unregister in the reverse order of registration.
	"""
	global addon_keymaps
	for keymap, keymap_item in addon_keymaps:
		keymap.keymap_items.remove(keymap_item)

	bpy.types.VIEW3D_MT_object.remove(object_menu_func) # Remove from Object Mode topbar Object menu.
	bpy.types.VIEW3D_MT_pose.remove(pose_menu_func) # Remove from Pose Mode topbar Pose menu.

	for cls in addon_classes:
		bpy.utils.unregister_class(cls)

if __name__ == "__main__":
	# Allows the script to be run directly in the text editor.
	register()
