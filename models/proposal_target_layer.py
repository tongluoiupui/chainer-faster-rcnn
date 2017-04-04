#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Mofidied by:
# Copyright (c) 2016 Shunta Saito

# Original work by:
# --------------------------------------------------------
# Faster R-CNN
# Copyright (c) 2015 Microsoft
# Licensed under The MIT License [see LICENSE for details]
# Written by Ross Girshick and Sean Bell
# https://github.com/rbgirshick/py-faster-rcnn
# --------------------------------------------------------

import numpy as np

from chainer import cuda
from models.anchor_target_layer import AnchorTargetLayer
from models.bbox_transform import bbox_transform


class ProposalTargetLayer(AnchorTargetLayer):
    """Assign proposals to ground-truth targets

    Generates training targets/labels for each object proposal: classification
    labels 0 - K (bg or object class 1, ... , K) and bbox regression targets in
    that case that the label is > 0.

    It produces:
        1. classification labels for each proposal
        2. proposed bounding-box regression targets.

    Args:
        feat_stride (int): The stride of corresponding pixels in the lowest
            layer (image). A couple of adjacent values on the input feature map
            are actually distant from each other with `feat_stride` pixels in
            the image plane.
        scales (list of integers): A list of scales of anchor boxes.

    """

    FG_THRESH = 0.5
    BG_THRESH_HI = 0.5
    BG_THRESH_LO = 0.1
    ROIS_PER_IMAGE = 128
    FG_FRACTION = 0.25

    def __init__(self, feat_stride=16, anchor_ratios=(0.5, 1, 2),
                 anchor_scales=(8, 16, 32), num_classes=21):
        super(ProposalTargetLayer, self).__init__(
            feat_stride, anchor_ratios, anchor_scales)
        self._num_classes = num_classes
        self._n_fg_rois = int(self.FG_FRACTION * self.ROIS_PER_IMAGE)

    def __call__(self, proposals, gt_boxes):
        """It takes numpy or cupy arrays

        Args:
            proposals (:class:`~numpy.ndarray`):
                :math:`(n_proposals, 4)`-shaped array, each of which has
                :math:`(x_0, y_0, x_1, y_1)` that represents each proposed RoI.
                These proposals come from RegionProposalNetwork.
            gt_boxes (:class:`~numpy.ndarray`):
                A :math:`(n_gt_boxes, 4)`-shaped array, each of which is a
                4-dimensional vector that represents
                :math:`(x1, y1, x2, y2, cls_id)` of each ground truth bbox.
                The scale of them are at the input image scale.
        """
        xp = cuda.get_array_module(proposals)
        proposals = cuda.to_cpu(proposals)
        gt_boxes = cuda.to_cpu(gt_boxes)

        argmax_overlaps_inds, max_overlaps, gt_argmax_overlaps_inds = \
            self._calc_overlaps(proposals, gt_boxes, np.arange(len(proposals)))

        # Select target candidate class labels
        cls_labels = gt_boxes[argmax_overlaps_inds, 4]

        # Select foreground RoIs as those with >= FG_THRESH overlap with any GT
        fg_inds = xp.where(max_overlaps >= self.FG_THRESH)[0]
        # Guard against when an image has more than n_fg_rois foreground RoIs
        n_fg_rois_per_image = min(self._n_fg_rois, fg_inds.size)
        # Sample foreground regions without replacement
        if fg_inds.size > 0:
            fg_inds = np.random.choice(
                fg_inds, size=n_fg_rois_per_image, replace=False)

        # Select background RoIs as those within [BG_THRESH_LO, BG_THRESH_HI)
        bg_inds = xp.where((max_overlaps < self.BG_THRESH_HI) &
                           (max_overlaps >= self.BG_THRESH_LO))[0]
        # Guard against there being more than desired
        n_bg_rois_per_image = self.ROIS_PER_IMAGE - n_fg_rois_per_image
        n_bg_rois_per_image = min(n_bg_rois_per_image, bg_inds.size)
        # Sample background regions without replacement
        if bg_inds.size > 0:
            bg_inds = np.random.choice(
                bg_inds, size=n_bg_rois_per_image, replace=False)

        # The indices that we're selecting (both fg and bg)
        keep_inds = np.append(fg_inds, bg_inds)
        # Select sampled values from cls_labels
        cls_labels = cls_labels[keep_inds]
        # Clamp labels for the background RoIs to 0
        cls_labels[n_fg_rois_per_image:] = 0
        # Select sampled values from proposals
        proposals = proposals[keep_inds]

        use_gt_boxes = gt_boxes[argmax_overlaps_inds][keep_inds]
        bbox_reg_targets = bbox_transform(proposals, use_gt_boxes)

        # Convert bbox_reg_targets into class-wise form
        use_gt_boxes = cuda.to_cpu(use_gt_boxes)
        bbox_reg_targets = cuda.to_cpu(bbox_reg_targets)
        ext_bbox_reg_targets = np.zeros(
            (len(keep_inds), 4 * self._num_classes), dtype=np.float32)
        object_inds = np.where(use_gt_boxes[:, -1] > 0)[0]
        for ind in object_inds:
            cls_pos = 4 * use_gt_boxes[ind, -1]
            ext_bbox_reg_targets[ind, cls_pos:cls_pos + 4] = bbox_reg_targets[ind]

        use_gt_boxes = xp.asarray(use_gt_boxes)
        ext_bbox_reg_targets = xp.asarray(ext_bbox_reg_targets)

        return use_gt_boxes, ext_bbox_reg_targets, keep_inds