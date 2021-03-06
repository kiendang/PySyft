import unittest.mock as mock

import pytest
import torch as th
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

import syft as sy
from itertools import starmap
from syft.generic.frameworks.types import FrameworkTensor
from syft.execution.placeholder import PlaceHolder
from syft.execution.plan import Plan
from syft.serde.msgpack import serde
from syft.serde.serde import deserialize
from syft.serde.serde import serialize


def test_plan_built_automatically():
    @sy.func2plan(args_shape=[(1,)])
    def plan_abs(data):
        return data.abs()

    assert isinstance(plan_abs.__str__(), str)
    assert len(plan_abs.actions) > 0
    assert plan_abs.is_built


def test_plan_build():
    @sy.func2plan(args_shape=())
    def plan_abs(data):
        return data.abs()

    assert not plan_abs.is_built
    assert not len(plan_abs.actions)

    plan_abs.build(th.tensor([-1]))

    assert len(plan_abs.actions)
    assert plan_abs.is_built


def test_tracing_torch():
    @sy.func2plan()
    def plan_torch(x, torch=th):
        a = torch.rand([2])
        x = torch.mul(a, x)
        return torch.split(x, 2)

    plan_torch.build(th.tensor([1, 2]))
    plan_torch.forward = None
    res = plan_torch(th.tensor([1, 2]))

    assert len(plan_torch.actions) == 3
    assert len(res) == 2


def test_plan_built_automatically_with_any_dimension():
    @sy.func2plan(args_shape=[(-1, 1)])
    def plan_abs(data):
        return data.abs()

    assert isinstance(plan_abs.__str__(), str)
    assert len(plan_abs.actions) > 0


def test_raise_exception_for_invalid_shape():

    with pytest.raises(ValueError):

        @sy.func2plan(args_shape=[(1, -20)])
        def _(data):
            return data  # pragma: no cover


def test_raise_exception_when_sending_unbuilt_plan(workers):
    bob = workers["bob"]

    @sy.func2plan()
    def plan(data):
        return data  # pragma: no cover

    with pytest.raises(RuntimeError):
        plan.send(bob)


def test_plan_execute_locally():
    @sy.func2plan(args_shape=[(1,)])
    def plan_abs(data):
        return data.abs()

    x = th.tensor([-1, 2, 3])
    x_abs = plan_abs(x)
    assert (x_abs == th.tensor([1, 2, 3])).all()


def test_plan_execute_locally_ambiguous_output(workers):
    bob, alice = workers["bob"], workers["alice"]

    @sy.func2plan(args_shape=[(1,)])
    def serde_plan(x):
        x = x + x
        y = x * 2
        return x

    serde_plan_simplified = serde._simplify(bob, serde_plan)
    serde_plan_detailed = serde._detail(bob, serde_plan_simplified)
    t = th.tensor([2.3])
    expected = serde_plan(t)
    actual = serde_plan_detailed(t)
    assert actual == expected


def test_plan_execute_locally_ambiguous_input(workers):
    bob, alice = workers["bob"], workers["alice"]

    @sy.func2plan(args_shape=[(1,), (1,), (1,)])
    def serde_plan(x, y, z):
        a = x + x  # 2
        b = x + z  # 4
        c = y + z  # 5
        return c, b, a  # 5, 4, 2

    serde_plan_simplified = serde._simplify(bob, serde_plan)
    serde_plan_detailed = serde._detail(bob, serde_plan_simplified)
    t1, t2, t3 = th.tensor([1]), th.tensor([2]), th.tensor([3])
    expected = serde_plan(t1, t2, t3)
    actual = serde_plan_detailed(t1, t2, t3)
    assert actual == expected


def test_plan_torch_function_no_args(workers):
    bob, alice = workers["bob"], workers["alice"]

    @sy.func2plan(args_shape=[(1,)])
    def serde_plan(x, torch=th):
        y = torch.tensor([-1])
        z = x + y
        return z

    serde_plan_simplified = serde._simplify(bob, serde_plan)
    serde_plan_detailed = serde._detail(bob, serde_plan_simplified)

    t = th.tensor([1.0])
    expected = serde_plan(t)
    actual = serde_plan_detailed(t)
    assert actual == expected == th.tensor([0.0])

    @sy.func2plan(args_shape=[(1,)])
    def serde_plan(x, torch=th):
        y = torch.arange(3)
        z = y + x
        return z

    serde_plan_simplified = serde._simplify(bob, serde_plan)
    serde_plan_detailed = serde._detail(bob, serde_plan_simplified)

    t = th.tensor([1.0])
    expected = serde_plan(t)
    actual = serde_plan_detailed(t)
    assert (actual == expected).all()
    assert (actual == th.tensor([1, 2, 3])).all()

    @sy.func2plan(args_shape=[(1,)])
    def serde_plan(x, torch=th):
        torch.manual_seed(14)
        y = torch.randint(2, size=(1,), dtype=torch.uint8)
        y = y + 10
        return y

    serde_plan_simplified = serde._simplify(bob, serde_plan)
    serde_plan_detailed = serde._detail(bob, serde_plan_simplified)

    t = th.tensor([1.0])
    expected = serde_plan(t)
    actual = serde_plan_detailed(t)
    assert actual == expected and actual >= 10


def test_plan_with_comp(workers):
    bob, alice = workers["bob"], workers["alice"]

    @sy.func2plan(args_shape=[(2,), (2,)])
    def serde_plan(x, y):
        z = x > y
        return z

    serde_plan_simplified = serde._simplify(bob, serde_plan)
    serde_plan_detailed = serde._detail(bob, serde_plan_simplified)

    t1 = th.tensor([2.0, 0.0])
    t2 = th.tensor([1.0, 1.0])
    expected = serde_plan_detailed(t1, t2)
    actual = serde_plan_detailed(t1, t2)
    assert (actual == expected).all()


def test_plan_fixed_len_loop(workers):
    bob, alice = workers["bob"], workers["alice"]

    @sy.func2plan(args_shape=[(1,)])
    def serde_plan(x):
        for i in range(10):
            x = x + 1
        return x

    serde_plan_simplified = serde._simplify(bob, serde_plan)
    serde_plan_detailed = serde._detail(bob, serde_plan_simplified)

    t = th.tensor([1.0])
    expected = serde_plan_detailed(t)
    actual = serde_plan_detailed(t)
    assert actual == expected


def test_plan_several_output_action(workers):
    bob, alice = workers["bob"], workers["alice"]

    @sy.func2plan(args_shape=[(4,)])
    def serde_plan(x, torch=th):
        y, z = torch.split(x, 2)
        return y + z

    serde_plan_simplified = serde._simplify(bob, serde_plan)
    serde_plan_detailed = serde._detail(bob, serde_plan_simplified)

    t = th.tensor([1, 2, 3, 4])
    expected = serde_plan_detailed(t)
    actual = serde_plan_detailed(t)
    assert (actual == th.tensor([4, 6])).all()
    assert (actual == expected).all()


def test_plan_method_execute_locally(hook):
    class Net(sy.Plan):
        def __init__(self):
            super(Net, self).__init__()
            self.fc1 = nn.Linear(2, 3)
            self.fc2 = nn.Linear(3, 2)
            self.fc3 = nn.Linear(2, 1)

        def forward(self, x, torch=th):
            x = torch.nn.functional.relu(self.fc1(x))
            x = self.fc2(x)
            x = self.fc3(x)
            return torch.nn.functional.log_softmax(x, dim=0)

    model = Net()

    model.build(th.tensor([1.0, 2]))

    # Call one time
    assert model(th.tensor([1.0, 2])) == 0

    # Call one more time
    assert model(th.tensor([1.0, 2.1])) == 0


def test_plan_multiple_send(workers):
    bob, alice = workers["bob"], workers["alice"]

    @sy.func2plan(args_shape=[(1,)])
    def plan_abs(data):
        return data.abs()

    plan_ptr = plan_abs.send(bob)
    x_ptr = th.tensor([-1, 7, 3]).send(bob)
    p = plan_ptr(x_ptr)
    x_abs = p.get()

    assert (x_abs == th.tensor([1, 7, 3])).all()

    # Test get / send plan
    plan_ptr = plan_abs.send(alice)

    x_ptr = th.tensor([-1, 2, 3]).send(alice)
    p = plan_ptr(x_ptr)
    x_abs = p.get()
    assert (x_abs == th.tensor([1, 2, 3])).all()


def test_plan_built_on_class(hook):
    """
    Test class Plans and plan send / get / send
    """

    x11 = th.tensor([-1, 2.0]).tag("input_data")
    x21 = th.tensor([-1, 2.0]).tag("input_data")

    device_1 = sy.VirtualWorker(hook, id="device_1", data=(x11,))
    device_2 = sy.VirtualWorker(hook, id="device_2", data=(x21,))

    class Net(sy.Plan):
        def __init__(self):
            super(Net, self).__init__()
            self.fc1 = nn.Linear(2, 3)
            self.fc2 = nn.Linear(3, 1)

            self.bias = th.tensor([1000.0])

        def forward(self, x, torch=th):
            x = torch.nn.functional.relu(self.fc1(x))
            x = self.fc2(x)
            return torch.nn.functional.log_softmax(x, dim=0) + self.bias

    net = Net()

    # build
    net.build(th.tensor([1, 2.0]))

    net_ptr = net.send(device_1)
    pointer_to_data = hook.local_worker.request_search("input_data", location=device_1)[0]
    pointer_to_result = net_ptr(pointer_to_data)

    result = pointer_to_result.get()
    assert isinstance(result, th.Tensor)
    assert result == th.tensor([1000.0])

    net_ptr = net.send(device_2)

    pointer_to_data = hook.local_worker.request_search("input_data", location=device_2)[0]
    pointer_to_result = net_ptr(pointer_to_data)

    result = pointer_to_result.get()
    assert isinstance(result, th.Tensor)
    assert result == th.tensor([1000.0])


def test_multiple_workers(workers):
    bob, alice = workers["bob"], workers["alice"]

    @sy.func2plan(args_shape=[(1,)])
    def plan_abs(data):
        return data.abs()

    plan_ptr = plan_abs.send(bob, alice)
    x_ptr = th.tensor([-1, 7, 3]).send(bob)
    p = plan_ptr(x_ptr)
    x_abs = p.get()
    assert (x_abs == th.tensor([1, 7, 3])).all()

    x_ptr = th.tensor([-1, 9, 3]).send(alice)
    p = plan_ptr(x_ptr)
    x_abs = p.get()
    assert (x_abs == th.tensor([1, 9, 3])).all()


def test_fetch_plan(hook, workers):
    alice = workers["alice"]

    @sy.func2plan(args_shape=[(1,)])
    def plan(data):
        return data * 3

    plan.send(alice)

    # Fetch plan
    fetched_plan = plan.owner.fetch_plan(plan.id, alice)

    # Execute it locally
    x = th.tensor([-1.0, 2, 3])
    assert (plan(x) == th.tensor([-3.0, 6, 9])).all()
    assert (fetched_plan(x) == th.tensor([-3.0, 6, 9])).all()
    assert fetched_plan.forward is None
    assert fetched_plan.is_built


@pytest.mark.parametrize("is_func2plan", [True, False])
def test_fetch_plan_multiple_times(hook, is_func2plan, workers):

    alice, bob, charlie, james = (
        workers["alice"],
        workers["bob"],
        workers["charlie"],
        workers["james"],
    )

    if is_func2plan:

        @sy.func2plan(args_shape=[(1,)], state=(th.tensor([3.0]),))
        def plan(data, state):
            (bias,) = state.read()
            return data * bias

    else:

        class Net(sy.Plan):
            def __init__(self):
                super(Net, self).__init__()
                self.fc1 = nn.Linear(1, 1)

            def forward(self, x):
                return self.fc1(x)

        plan = Net()
        plan.build(th.tensor([1.2]))

    plan_pointer = plan.send(james)

    # Fetch plan
    fetched_plan = plan_pointer.owner.fetch_plan(plan_pointer.id_at_location, james, copy=True)

    # Execute the fetch plan
    x = th.tensor([-1.0])
    result1 = fetched_plan(x)

    # 2. Re-fetch Plan
    fetched_plan = plan_pointer.owner.fetch_plan(plan_pointer.id_at_location, james, copy=True)

    # Execute the fetch plan
    x = th.tensor([-1.0])
    result2 = fetched_plan(x)

    assert th.all(result1 - result2 < 1e-2)


def test_fetch_plan_remote(hook, start_remote_worker):

    server, remote_proxy = start_remote_worker(id="test_fetch_plan_remote", hook=hook, port=8803)

    @sy.func2plan(args_shape=[(1,)], state=(th.tensor([1.0]),))
    def plan_mult_3(data, state):
        (bias,) = state.read()
        return data * 3 + bias

    plan_mult_3.send(remote_proxy)

    # Fetch plan
    fetched_plan = plan_mult_3.owner.fetch_plan(plan_mult_3.id, remote_proxy)

    # Execute it locally
    x = th.tensor([-1.0, 2, 3])
    assert (plan_mult_3(x) == th.tensor([-2.0, 7, 10])).all()
    assert (fetched_plan(x) == th.tensor([-2.0, 7, 10])).all()
    assert fetched_plan.forward is None
    assert fetched_plan.is_built

    remote_proxy.close()
    server.terminate()


def test_plan_serde(hook):
    @sy.func2plan(args_shape=[(1, 3)])
    def my_plan(data):
        x = data * 2
        y = (x - 2) * 10
        return x + y

    serialized_plan = serialize(my_plan)
    deserialized_plan = deserialize(serialized_plan)

    x = th.tensor([-1, 2, 3])
    assert (deserialized_plan(x) == th.tensor([-42, 24, 46])).all()


def test_execute_plan_remotely(hook, start_remote_worker):
    """Test plan execution remotely."""

    @sy.func2plan(args_shape=[(1,)])
    def my_plan(data):
        x = data * 2
        y = (x - 2) * 10
        return x + y

    x = th.tensor([-1, 2, 3])
    local_res = my_plan(x)

    server, remote_proxy = start_remote_worker(id="test_plan_worker", hook=hook, port=8799)

    plan_ptr = my_plan.send(remote_proxy)
    x_ptr = x.send(remote_proxy)
    ptr = plan_ptr(x_ptr)
    assert isinstance(ptr, FrameworkTensor) and ptr.is_wrapper
    plan_res = ptr.get()

    assert (plan_res == local_res).all()

    # delete remote object before websocket connection termination
    del x_ptr

    remote_proxy.close()
    server.terminate()


def test_execute_plan_module_remotely(hook, start_remote_worker):
    """Test plan execution remotely."""

    class Net(sy.Plan):
        def __init__(self):
            super(Net, self).__init__()
            self.fc1 = nn.Linear(2, 3)
            self.fc2 = nn.Linear(3, 2)

            self.bias = th.tensor([1000.0])

        def forward(self, x):
            x = F.relu(self.fc1(x))
            x = self.fc2(x)
            return F.log_softmax(x, dim=0) + self.bias

    net = Net()

    x = th.tensor([-1, 2.0])
    local_res = net(x)
    assert not net.is_built

    net.build(x)

    server, remote_proxy = start_remote_worker(id="test_plan_worker_2", port=8799, hook=hook)

    plan_ptr = net.send(remote_proxy)
    x_ptr = x.send(remote_proxy)
    ptr = plan_ptr(x_ptr)
    assert isinstance(ptr, FrameworkTensor) and ptr.is_wrapper
    remote_res = ptr.get()

    assert (remote_res == local_res).all()

    # delete remote object before websocket connection termination
    del x_ptr

    remote_proxy.close()
    server.terminate()


def test_train_plan_locally_and_then_send_it(hook, start_remote_worker):
    """Test training a plan locally and then executing it remotely."""

    # Create toy model
    class Net(sy.Plan):
        def __init__(self):
            super(Net, self).__init__()
            self.fc1 = nn.Linear(2, 3)
            self.fc2 = nn.Linear(3, 2)

        def forward(self, x):
            x = F.relu(self.fc1(x))
            x = self.fc2(x)
            return F.log_softmax(x, dim=0)

    net = Net()

    # Create toy data
    x = th.tensor([-1, 2.0])
    y = th.tensor([1.0])

    # Train Model
    opt = optim.SGD(params=net.parameters(), lr=0.01)
    previous_loss = None

    for _ in range(5):
        # 1) erase previous gradients (if they exist)
        opt.zero_grad()

        # 2) make a prediction
        pred = net(x)

        # 3) calculate how much we missed
        loss = ((pred - y) ** 2).sum()

        # 4) figure out which weights caused us to miss
        loss.backward()

        # 5) change those weights
        opt.step()

        if previous_loss is not None:
            assert loss < previous_loss

        previous_loss = loss

    local_res = net(x)
    net.build(x)

    server, remote_proxy = start_remote_worker(id="test_plan_worker_3", port=8800, hook=hook)

    plan_ptr = net.send(remote_proxy)
    x_ptr = x.send(remote_proxy)
    remote_res = plan_ptr(x_ptr).get()

    assert (remote_res == local_res).all()

    # delete remote object before websocket connection termination
    del x_ptr

    remote_proxy.close()
    server.terminate()


def test_cached_plan_send(workers):
    bob = workers["bob"]

    @sy.func2plan(args_shape=[(1,)])
    def plan_abs(data):
        return data.abs()

    plan_bob_ptr1 = plan_abs.send(bob)
    plan_bob_ptr2 = plan_abs.send(bob)
    pointers = plan_abs.get_pointers()

    assert len(pointers) == 1
    assert plan_bob_ptr1 is plan_bob_ptr2


def test_cached_multiple_location_plan_send(workers):
    bob, alice = workers["bob"], workers["alice"]

    @sy.func2plan(args_shape=[(1,)])
    def plan_abs(data):
        return data.abs()

    plan_group_ptr1 = plan_abs.send(bob, alice)
    plan_group_ptr2 = plan_abs.send(bob, alice)

    pointers = plan_abs.get_pointers()

    assert len(pointers) == 2


def test_plan_input_usage(hook):
    x11 = th.tensor([-1, 2.0]).tag("input_data")
    x12 = th.tensor([1, -2.0]).tag("input_data2")

    device_1 = sy.VirtualWorker(hook, id="test_dev_1", data=(x11, x12))

    @sy.func2plan()
    def plan_test_1(x, y):
        return x

    @sy.func2plan()
    def plan_test_2(x, y):
        return y

    pointer_to_data_1 = device_1.search("input_data")[0]
    pointer_to_data_2 = device_1.search("input_data2")[0]

    plan_test_1.build(th.tensor([1.0, -2.0]), th.tensor([1, 2]))
    pointer_plan = plan_test_1.send(device_1)
    pointer_to_result = pointer_plan(pointer_to_data_1, pointer_to_data_2)
    result = pointer_to_result.get()
    assert (result == x11).all()

    plan_test_2.build(th.tensor([1.0, -2.0]), th.tensor([1, 2]))
    pointer_plan = plan_test_2.send(device_1)
    pointer_to_result = pointer_plan(pointer_to_data_1, pointer_to_data_2)
    result = pointer_to_result.get()
    assert (result == x12).all
