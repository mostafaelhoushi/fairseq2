# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Final, Iterable, Optional, Protocol, Sequence

from torch import Tensor
from torch.distributed.fsdp import FullyShardedDataParallel as FSDP
from torch.distributed.fsdp.api import (
    BackwardPrefetch,
    CPUOffload,
    ShardedOptimStateDictConfig,
    ShardedStateDictConfig,
    ShardingStrategy,
    StateDictType,
)
from torch.nn import Module, Parameter

from fairseq2.gang import Gang
from fairseq2.nn.utils.module import (
    infer_device,
    reset_non_persistent_buffers,
    reset_parameters,
    select_parameters,
    to_empty,
)
from fairseq2.typing import META, Device


def to_fsdp(
    module: Module,
    gang: Gang,
    wrap_policy: FSDPWrapPolicy,
    *,
    ignored_param_names: Optional[Sequence[str]] = None,
    skip_init: bool = False,
    broadcast_state: bool = False,
    sharding_strategy: ShardingStrategy = ShardingStrategy.FULL_SHARD,
    memory_policy: Optional[FSDPMemoryPolicy] = None,
    static_graph: bool = False,
) -> FSDP:
    """Wrap ``module`` with FSDP.

    :param module:
        The module to be wrapped with FSDP.
    :param gang:
        The gang over which the module will be sharded.
    :param wrap_policy:
        The policy to apply FSDP to submodules of ``module``.
    :param ignored_param_names:
        The ignored parameter names, can contain regular expressions.
    :param skip_init:
        If ``True``, skips initializing the parameters and buffers moved from
        the meta device onto the device of ``gang``.
    :param broadcast_state:
        If ``True``, each FSDP module will broadcast its parameters and buffers
        from rank 0 to ensure that they are replicated across all processes.
    :param sharding_strategy:
        The sharding strategy to trade off memory saving and communication
        overhead.
    :param memory_policy:
        The policy to instruct FSDP when and how to allocate memory.
    :param static_graph:
        If ``True``, indicates that ``module`` has a static computation graph.
        Must be ``True`` for forward prefetching to be in effect.
    """
    if memory_policy is None:
        memory_policy = FSDP_STANDARD_MEMORY_POLICY

    if infer_device(module) == META:
        broadcast_state = False

        param_init_fn = FSDPParameterInitializer(gang.device, skip_init)
    else:
        param_init_fn = None

    fsdp = FSDP(
        module,
        process_group=gang.as_process_group(),
        sharding_strategy=sharding_strategy,
        cpu_offload=CPUOffload() if memory_policy.cpu_offload else None,
        auto_wrap_policy=wrap_policy,
        backward_prefetch=memory_policy.backward_prefetch,
        param_init_fn=param_init_fn,
        device_id=gang.device,
        sync_module_states=broadcast_state,
        forward_prefetch=static_graph and memory_policy.forward_prefetch,
        limit_all_gathers=memory_policy.limit_all_gathers,
        use_orig_params=True,
        ignored_states=get_ignored_parameters(module, ignored_param_names),
    )

    FSDP.set_state_dict_type(
        fsdp,
        StateDictType.SHARDED_STATE_DICT,
        state_dict_config=ShardedStateDictConfig(offload_to_cpu=True),
        optim_state_dict_config=ShardedOptimStateDictConfig(offload_to_cpu=True),
    )

    return fsdp


class FSDPWrapPolicy(Protocol):
    """Represents an FSDP wrap policy."""

    def __call__(self, module: Module, recurse: bool, non_wrapped_numel: int) -> bool:
        """
        :param module:
            The module to apply the policy to.
        :param recurse:
            If ``False``, the return value specifies whether ``module`` should
            have FSDP applied; if ``True``, the return value specifies whether
            the traversal should continue into the module's subtree.
        :param non_wrapped_numel:
            The number of elements that have not yet been wrapped.

        :returns:
            See the description of the ``recurse`` parameter.
        """


@dataclass
class FSDPMemoryPolicy:
    """Specifies the device memory usage policy of an FSDP module."""

    forward_prefetch: bool
    """If ``True``, FSDP explicitly prefetches the next forward-pass all-gather
    before the current forward computation. For more information, check out the
    same named parameter of :class:`FSDP`."""

    backward_prefetch: Optional[BackwardPrefetch]
    """The backward prefetching mode of all-gathers. For more information, check
    out the same named parameter of :class:`FSDP`."""

    limit_all_gathers: bool
    """If ``True``, FSDP explicitly synchronizes the CPU thread to ensure GPU
    memory use from only two consecutive FSDP instances. For more information,
    check out the same named parameter of :class:`FSDP`."""

    cpu_offload: bool
    """If ``True``, FSDP offloads parameters not involved in computation to CPU.
    For more information, check out :class:`CPUOffload`."""


FSDP_STANDARD_MEMORY_POLICY: Final = FSDPMemoryPolicy(
    forward_prefetch=True,
    backward_prefetch=BackwardPrefetch.BACKWARD_PRE,
    limit_all_gathers=False,
    cpu_offload=False,
)
"""Enables both forward and backward prefetching, puts no limit on communication
and computation overlap."""


FSDP_LOW_MEMORY_POLICY: Final = FSDPMemoryPolicy(
    forward_prefetch=False,
    backward_prefetch=BackwardPrefetch.BACKWARD_POST,
    limit_all_gathers=True,
    cpu_offload=False,
)
"""Disables forward prefetching, enables backward prefetching with low-memory
pressure, rate-limits communication and computation overlap."""


FSDP_VERY_LOW_MEMORY_POLICY: Final = FSDPMemoryPolicy(
    forward_prefetch=False,
    backward_prefetch=None,
    limit_all_gathers=True,
    cpu_offload=True,
)
"""Disables both forward and backward prefetching, disables communication and
computation overlap, offloads parameters to CPU."""


def get_ignored_parameters(
    module: Module, names: Optional[Sequence[str]]
) -> Optional[Iterable[Parameter]]:
    """Get the list of parameters that should be ignored by FSDP.

    :param module:
        The module to be wrapped with FSDP.
    :param names:
        The ignored parameter names, can contain regular expressions.
    """
    if names is None:
        return None

    return (p for _, p in select_parameters(module, names))


class FSDPParameterInitializer:
    """Initializes the parameters and buffers of an FSDP module.

    This is a convenience callable to pass to the ``param_init_fn`` parameter of
    :class:`FSDP`. It moves the parameters and buffers residing on a meta device
    onto ``device`` and initializes them.

    Usage:

    >>> model = MyModel(..., device=Device("meta"))
    >>>
    >>> fsdp_model = FullyShardedDataParallel(
    ...     ..., param_init_fn=FSDPParameterInitializer(Device("cuda:0"))
    ... )
    """

    memo: Dict[Tensor, Tensor]
    device: Device
    skip_init: bool

    def __init__(self, device: Device, skip_init: bool = False) -> None:
        """
        :param device:
            The device onto which to move the parameters and buffers.
        :param skip_init:
            If ``True``, skips initializing the parameters and buffers after
            moving them onto ``device``. The non-persistent buffers are always
            initialized regardless of ``skip_init``.
        """
        self.memo = {}
        self.device = device
        self.skip_init = skip_init

    def __call__(self, module: Module) -> None:
        """
        :param module:
            An FSDP module or submodule.
        """
        to_empty(module, self.device, recurse=False, memo=self.memo)

        if not self.skip_init:
            reset_parameters(module, recurse=False)
        else:
            reset_non_persistent_buffers(module, recurse=False)
